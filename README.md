# はじめに

AWS LambdaとPythonを利用してノートテイキングアプリ[Evernote](https://evernote.com/)のRestful APIを実装し、ほかのLambdaや外部システムから呼び出せるようにした「restfulapiforevernote」を作った際のメモ。

Evernoteにはもともと各種言語のサードパーティアプリに組み込んでEvernote APIにアクセス可能とするパッケージが[公式](http://dev.evernote.com/doc/)で提供されているが、より簡単かつ汎用的に外部システムと連携ができる仕組みであるRestful APIは提供されていなかったため、Evernote APIを利用するシステムごとにパッケージを組み込む必要があった。

そこでEvernoteパッケージとノート検索、作成、更新などの主要な処理をAWS Lambda上でマイクロサービス化し、API GatewayでRestful APIとして開放して、ほかのシステムからRESTfulな接続だけでEvernote上のデータにアクセスできるようにした。

飽くまで内部向けで野良APIとして公開はしていないので、利用したい場合は構築手順に従って各自構築すること。コードがスパゲッティ、一部機能が未実装、エラー処理が甘い、検索のレスポンスがJSON形式ではない、など改善点は多々あるが、APIリファレンスも記載するのでそれに従えば動きはする。改善でき次第随時反映していく。

# 前提環境

- [Evernote]()
- [Amazon Web Serbices](https://aws.amazon.com)
- [AWS CLI](https://aws.amazon.com/jp/cli/)
- [Docker](https://www.docker.com/)
- [jq](https://stedolan.github.io/jq/)コマンド、[git](https://git-scm.com/)コマンド
- 構築手順はAmazon Linux 2（[AWS Cloud9](https://aws.amazon.com/jp/cloud9/)）上で検証した
- 構築手順のうち[アクセストークン取得]はMac OS上で検証した。

# 構築手順

- 変数初期化

```shell
$ app_name=restfulapiforevernote
$ workdir=${PWD}/$app_name
$ backet_name=${app_name}-$(echo -n $(aws sts get-caller-identity | jq -r .Account) | md5sum | cut -c 1-10)
```

- 構築用プログラム一式を作成する。（[github](https://github.com/r-wakatsuki/restfulapiforevernote)にも上げてあるので以下のコマンドでクローンしてもOK）

```shell
$ git clone https://github.com/r-wakatsuki/${app_name}.git $workdir
```

- プログラム一式のファイルパスは以下の通り。

```
restfulapiforevernote/
　├ restfulapiforevernote-aws-function-00/
　│　└ lambda_function.py
　├ restfulapiforevernote-aws-layer-00/
　│　└ Dockerfile
　└ restfulapiforevernote-aws-stack-00.yml
```

- Dockerfile

evernote3のパッケージが含まれたLambda Layer用のzipを作成するためのDockerfile。

```Dockerfile
FROM python:3.7
WORKDIR /work

CMD apt update && \
    apt install -y zip && \
    mkdir python && \
    pip install -t ./python evernote3 oauth2 && \
    zip -r ./zipdir/layer.zip python
```

- lambda_function.py

Lambdaに配置するコード。restfulapiforevernoteのバックエンドとして実際にEvernote APIへのアクセスを行う。

```lambda_function.py
from base64 import b64decode
import json,re,os,boto3
from evernote.api.client import EvernoteClient
import evernote.edam.type.ttypes as Types
from evernote.edam.notestore.ttypes import NoteFilter, NotesMetadataResultSpec

def lambda_handler(event, context):

    #共通処理　事前定義
    def return200(res_body):
        return{
            'statusCode': 200,
            'body': json.dumps(res_body)
        }

    def return400(message_str):
        body = {
            'errorMessage': message_str
        }
        return{
            'statusCode': 400,
            'body': json.dumps(body)
        }

    global noteobject

    #共通処理　Evernote認証
    access_token = boto3.client('kms').decrypt(CiphertextBlob=b64decode(os.environ['en_access_token']))['Plaintext'].decode()
    client = EvernoteClient(token=access_token, sandbox=False)
    try:
        note_store = client.get_note_store()
    except Exception as e:
        return(return400(str(e)))

    #<ノート検索・複数取得><ノート個別取得>向け処理
    nhead_e1 = r'^<\?xml version="1.0" encoding="UTF-8"\?>(\n)?'
    nhead_e2 = r'^<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">(\n)?'
    nhead_e3 = r'^<en-note.*?>(\n)?'

    #API:<ノート検索・複数取得>
    if event['httpMethod'] == 'GET' and event['resource'] == '/notes':

        #<ノート検索・複数取得>向け処理　リクエストからパラメータ値を取得してセット
        req_query = event['queryStringParameters']
        if req_query is None:
            return(return400("パラメータが指定されていません"))
        req_searchword = req_query.get('q','')
        req_notebookguid = req_query.get('notebookguid','')
        try:
            req_searchtype = int(req_query.get('searchtype',0))
            req_includecontent = int(req_query.get('includecontent',0))
            req_maxnotescount = int(req_query.get('maxnotescount',1))
        except:
            return(return400('不正な型のパラメータ'))

        filter = NoteFilter(notebookGuid = req_notebookguid)
        resultSpec = NotesMetadataResultSpec(includeTitle=True,includeNotebookGuid=True)
        metalist = note_store.findNotesMetadata(filter, 0, 250, resultSpec)
        notelist = []

        for meta in metalist.notes:
            noteobject = ''
            if req_searchtype == 0: #タイトル部分一致（既定）
                if str(req_searchword) in meta.title:
                    if req_includecontent == 0: #コンテンツを含まない
                        noteobject = {
                            'noteguid' : meta.guid,
                            'title': meta.title,
                            'notebookguid' : meta.notebookGuid
                        }
                    if req_includecontent == 1: #コンテンツを含む
                        content = note_store.getNoteContent(access_token,meta.guid)
                        content = re.sub(nhead_e1, '', content)
                        content = re.sub(nhead_e2, '', content)
                        content = re.sub(nhead_e3, '', content)
                        content = re.sub(r'(\n)?</en-note>$', '', content)
                        noteobject = {
                            'noteguid' : meta.guid,
                            'title': meta.title,
                            'notebookguid' : meta.notebookGuid,
                            'content' : content,
                        }
            if req_searchtype == 1: #タイトル完全一致
                if req_searchword == meta.title:
                    if req_includecontent == 0: #コンテンツを含まない
                        noteobject = {
                            'noteguid' : meta.guid,
                            'title': meta.title,
                            'notebookguid' : meta.notebookGuid
                        }
                    if req_includecontent == 1: #コンテンツを含む
                        content = note_store.getNoteContent(access_token,meta.guid)
                        content = re.sub(nhead_e1, '', content)
                        content = re.sub(nhead_e2, '', content)
                        content = re.sub(nhead_e3, '', content)
                        content = re.sub(r'(\n)?</en-note>$', '', content)
                        noteobject = {
                            'noteguid' : meta.guid,
                            'title': meta.title,
                            'notebookguid' : meta.notebookGuid,
                            'content' : content
                        }
            if req_searchtype == 2: #タイトル・本文部分一致
                pass #作成中
            if noteobject != '':
                notelist.append(noteobject)
            if len(notelist) == req_maxnotescount:
                break

        return(return200(notelist))

    #API:<ノート個別取得>
    if event['httpMethod'] == 'GET' and event['resource'] == '/note/{noteguid}':

        #リクエストからパラメータ値を取得してセット
        try:
            req_noteguid = json.loads(event['pathParameters']).get('noteguid')
        except:
            req_noteguid = event['pathParameters'].get('noteguid')
        if req_noteguid is None:
            return(return400("{noteguid}パス未指定"))

        n = note_store.getNote(access_token,req_noteguid,False,False,False,False)
        content = note_store.getNoteContent(access_token,req_noteguid)
        content = re.sub(nhead_e1, '', content)
        content = re.sub(nhead_e2, '', content)
        content = re.sub(nhead_e3, '', content)
        content = re.sub(r'(\n)?</en-note>$', '', content)
        noteobject = {
            'noteguid' : n.guid,
            'title': n.title,
            'content' : content,
            'notebookguid' : n.notebookGuid
        }

        return(return200(noteobject))

    #<ノート作成><ノート更新><ノート削除>向け処理
    n = Types.Note()
    nhead_e = '<?xml version="1.0" encoding="UTF-8"?>'
    nhead_e += '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'

    #<ノート作成><ノート更新><ノート削除>向け処理 リクエストボディから値を取得してセット
    try:
        req_body_dict = json.loads(event['body'])
    except:
        return(return400('不正な形式のデータ'))
    req_notecontent = req_body_dict.get('notecontent','')
    req_resource_ary = req_body_dict.get('resource_ary',[])
    req_notetitle = req_body_dict.get('notetitle')
    req_notebookguid = req_body_dict.get('notebookguid','')
    if req_notetitle == None:
        return(return400('notetitleが未指定'))
    else:
        n.title = req_notetitle
    if req_notebookguid != '':
        n.notebookGuid = req_notebookguid
    else:
        pass

    #API:<ノート作成>
    if event['httpMethod'] == 'POST' and event['resource'] == '/note':
        n.content = nhead_e + '<en-note>' + req_notecontent + '</en-note>'
        if len(req_resource_ary) != 0:
            resources = []
            for index,item in enumerate(req_resource_ary):
                data = Types.Data()
                data.body = b64decode(item['databody'])
                data.size = len(data.body)
                data.bodyHash = item['bodyhash']
                resource = Types.Resource()
                resource.mime = item['mimetype']
                resource.data = data
                attr = Types.ResourceAttributes()
                attr.fileName = 'img' + str([index-1]) + '.jpg'
                resource.attributes = attr
                resources.append(resource)
            if len(resources) != 0:
                n.resources = resources
        n = note_store.createNote(n)
        noteobject = {
            'noteguid' : n.guid,
            'title': n.title,
            'notebookguid' : n.notebookGuid
        }
        return(return200(noteobject))

    #<ノート更新><ノート削除>向け処理 リクエストからパラメータ値を取得してセット
    try:
        req_noteguid = json.loads(event['pathParameters']).get('noteguid')
    except:
        req_noteguid = event['pathParameters'].get('noteguid')
    if req_noteguid is None:
        return(return400("{noteguid}パスが未指定"))

    #API:<ノート更新>
    if event['httpMethod'] == 'PATCH' and event['resource'] == '/note/{noteguid}':
        n.guid = req_noteguid
        if req_notecontent != '':
            n.content = nhead_e + '<en-note>' + req_notecontent + '</en-note>'
        if len(req_resource_ary) != 0:
            pass #作成中
        n = note_store.updateNote(access_token,n)
        noteobject = {
            'noteguid' : n.guid,
            'title': n.title,
            'notebookguid' : n.notebookGuid
        }
        return(return200(noteobject))

    #API:<ノート削除>
    if event['httpMethod'] == 'DELETE':
        pass #作成中

    return(return400('不正なメソッドおよびリソースの指定 %s, %s' % (event['resource'],event['httpMethod'])))
```

- restfulapiforevernote-aws-stack-00.yml

restfulapiforevernoteの構成要素となるLambda、Layer、API GatewayなどのAWSリソースをCloud Formationで作成するためのyamlテンプレート。

```restfulapiforevernote-aws-stack-00.yml
Parameters:
  appName:
    Type: String
  backetName:
    Type: String
  stageName:
    Type: String
    Default: dev
  usagePlanQuotaLimit:
    Type: Number
    Default: 200
  usagePlanQuotaPeriod:
    Type: String
    Default: MONTH
    AllowedValues:
      - DAY
      - WEEK
      - MONTH
  usagePlanThrottleBurstLimit:
    Type: Number
    Default: 10
  usagePlanThrottleRateLimit:
    Type: Number
    Default: 5
Resources:
  Role00:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub ${appName}-aws-role-00
      AssumeRolePolicyDocument:
        Version: 2012-10-17
        Statement:
          - Effect: Allow
            Principal:
              Service:
                - lambda.amazonaws.com
            Action:
              - 'sts:AssumeRole'
  Policy00:
    Type: AWS::IAM::Policy
    DependsOn:
      - Role00
    Properties:
      PolicyName: !Sub ${appName}-aws-policy-00
      PolicyDocument: 
        Version: "2012-10-17"
        Statement: 
          - 
            Effect: "Allow"
            Action: 
              - "logs:CreateLogGroup"
              - "logs:CreateLogStream"
              - "logs:PutLogEvents"
            Resource: "*"
      Roles:
        - !Ref Role00
  Key00:
    Type: AWS::KMS::Key
    DependsOn:
      - Role00
    Properties:
      KeyPolicy:
        Version: "2012-10-17"
        Id: !Sub ${appName}-aws-apikey-00-keypolicy
        Statement:
          -
            Sid: "Allow administration of the key"
            Effect: "Allow"
            Principal:
              AWS: !Sub arn:aws:iam::${AWS::AccountId}:root
            Action: "*"
            Resource: "*"
          -
            Sid: "Allow use of the key"
            Effect: "Allow"
            Principal:
              AWS: !GetAtt Role00.Arn
            Action: 
              - "kms:Encrypt"
              - "kms:Decrypt"
              - "kms:ReEncrypt*"
              - "kms:GenerateDataKey*"
              - "kms:DescribeKey"
            Resource: "*"
  Layer00:
    Type: AWS::Lambda::LayerVersion
    Properties:
      LayerName: !Sub ${appName}-aws-layer-00
      CompatibleRuntimes: 
        - python3.7
      Content: 
        S3Bucket: !Ref backetName
        S3Key: layer.zip
  Function00:
    Type: AWS::Lambda::Function
    DependsOn:
      - Key00
      - Layer00
    Properties:
      FunctionName: !Sub ${appName}-aws-function-00
      Code:
        S3Bucket: !Ref backetName
        S3Key: function.zip
      Handler: lambda_function.lambda_handler
      MemorySize: 512
      Timeout: 60
      Role: !GetAtt Role00.Arn
      Runtime: python3.7
      Layers:
        - !Ref Layer00
      Environment:
        Variables:
          en_access_token: DummyAccessToken
      KmsKeyArn: !GetAtt Key00.Arn
  Api00:
    Type: AWS::ApiGateway::RestApi
    Properties:
      Name: !Sub ${appName}-aws-api-00
  Resource00:
    Type: AWS::ApiGateway::Resource
    Properties:
      RestApiId: !Ref Api00
      ParentId: !GetAtt Api00.RootResourceId
      PathPart: note
  Resource01:
    Type: AWS::ApiGateway::Resource
    Properties:
      RestApiId: !Ref Api00
      ParentId: !GetAtt Api00.RootResourceId
      PathPart: notes
  Resource02:
    Type: AWS::ApiGateway::Resource
    Properties:
      RestApiId: !Ref Api00
      ParentId: !Ref Resource00
      PathPart: '{noteguid}'
  Method00:
    Type: AWS::ApiGateway::Method
    Properties:
      ApiKeyRequired: true
      RestApiId: !Ref Api00
      ResourceId: !Ref Resource00
      HttpMethod: ANY
      AuthorizationType: NONE
      MethodResponses: []
      Integration:
        Type: AWS_PROXY
        IntegrationHttpMethod: POST
        Uri: !Join 
        - ''
        - - 'arn:aws:apigateway:'
          - !Ref 'AWS::Region'
          - ':lambda:path/2015-03-31/functions/'
          - !GetAtt Function00.Arn
          - /invocations
        IntegrationResponses: []
        PassthroughBehavior: NEVER
  Method01:
    Type: AWS::ApiGateway::Method
    Properties:
      ApiKeyRequired: true
      RestApiId: !Ref Api00
      ResourceId: !Ref Resource01
      HttpMethod: ANY
      AuthorizationType: NONE
      MethodResponses: []
      Integration:
        Type: AWS_PROXY
        IntegrationHttpMethod: POST
        Uri: !Join 
        - ''
        - - 'arn:aws:apigateway:'
          - !Ref 'AWS::Region'
          - ':lambda:path/2015-03-31/functions/'
          - !GetAtt Function00.Arn
          - /invocations
        IntegrationResponses: []
        PassthroughBehavior: NEVER
  Method02:
    Type: AWS::ApiGateway::Method
    Properties:
      ApiKeyRequired: true
      RestApiId: !Ref Api00
      ResourceId: !Ref Resource02
      HttpMethod: ANY
      AuthorizationType: NONE
      MethodResponses: []
      Integration:
        Type: AWS_PROXY
        IntegrationHttpMethod: POST
        Uri: !Join 
        - ''
        - - 'arn:aws:apigateway:'
          - !Ref 'AWS::Region'
          - ':lambda:path/2015-03-31/functions/'
          - !GetAtt Function00.Arn
          - /invocations
        IntegrationResponses: []
        PassthroughBehavior: NEVER
  LambdaPermission00:
    Type: 'AWS::Lambda::Permission'
    Properties:
      FunctionName: !Ref Function00
      Action: 'lambda:InvokeFunction'
      Principal: apigateway.amazonaws.com
      SourceArn: !Join 
        - ''
        - - 'arn:aws:execute-api:'
          - !Ref 'AWS::Region'
          - ':'
          - !Ref 'AWS::AccountId'
          - ':'
          - !Ref Api00
          - /*/ANY/note
  LambdaPermission01:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref Function00
      Action: 'lambda:InvokeFunction'
      Principal: apigateway.amazonaws.com
      SourceArn: !Join 
        - ''
        - - 'arn:aws:execute-api:'
          - !Ref 'AWS::Region'
          - ':'
          - !Ref 'AWS::AccountId'
          - ':'
          - !Ref Api00
          - /*/ANY/notes
  LambdaPermission02:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref Function00
      Action: 'lambda:InvokeFunction'
      Principal: apigateway.amazonaws.com
      SourceArn: !Join 
        - ''
        - - 'arn:aws:execute-api:'
          - !Ref 'AWS::Region'
          - ':'
          - !Ref 'AWS::AccountId'
          - ':'
          - !Ref Api00
          - /*/ANY/note/*
  Deployment00:
    Type: AWS::ApiGateway::Deployment
    DependsOn:
      - Method00
      - Method01
      - Method02
    Properties:
      RestApiId: !Ref Api00
      StageName: !Ref stageName
  ApiKey00:
    Type: AWS::ApiGateway::ApiKey
    DependsOn:
      - Deployment00
    Properties: 
      Name: !Sub ${appName}-aws-apikey-00
      Enabled: true
      StageKeys: 
        - RestApiId: !Ref Api00
          StageName: !Ref stageName
  UsagePlan00:
    Type: AWS::ApiGateway::UsagePlan
    DependsOn:
      - ApiKey00
    Properties:
      UsagePlanName: !Sub ${appName}-aws-usageplan-00
      ApiStages:
      - ApiId: !Ref Api00
        Stage: !Ref stageName
      Quota:
        Limit: !Ref usagePlanQuotaLimit
        Period: !Ref usagePlanQuotaPeriod
      Throttle:
        BurstLimit: !Ref usagePlanThrottleBurstLimit
        RateLimit: !Ref usagePlanThrottleRateLimit
  UsagePlanKey00:
    Type: AWS::ApiGateway::UsagePlanKey
    DependsOn:
      - UsagePlan00
    Properties:
      KeyId: !Ref ApiKey00
      KeyType: API_KEY
      UsagePlanId: !Ref UsagePlan00
```

- Dockerを利用してLambda Layer用のzipを作成する。

```shell
$ cd $workdir
$ docker build -t layer_image ${workdir}/${app_name}-aws-layer-00
$ docker run -v ${workdir}:/work/zipdir layer_image
```

- Lambda関数コード用のzipを作成して、Layer用のzipと合わせてS3にアップロードする。

```shell
$ zip -j ${workdir}/function.zip ${workdir}/${app_name}-aws-function-00/*
$ aws s3 mb s3://$backet_name
$ aws s3 mv ${workdir}/function.zip s3://${backet_name}/function.zip
$ aws s3 mv ${workdir}/layer.zip s3://${backet_name}/layer.zip
```

- CloudFormationでAWSにデプロイ。

```shell
$ aws cloudformation create-stack --stack-name ${app_name}-aws-stack-00 \
  --template-body file://${workdir}/${app_name}-aws-stack-00.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters ParameterKey=appName,ParameterValue=$app_name \
  ParameterKey=backetName,ParameterValue=$backet_name
```

- アクセストークン取得

[EvernoteAPIを利用するためのサードパーティ用アクセストークンをOAuth認証で取得する](https://qiita.com/r-wakatsuki/items/42ff5e52c819fc7a02d5) をもとにEvernoteのサードパーティ用アクセストークンを取得する。

- アクセストークン設定

ブラウザから[AWS CloudFormationのコンソール](https://ap-northeast-1.console.aws.amazon.com/cloudformation/home)を開く。
スタック一覧から「restfulapiforevernote-aws-stack-00」を選択し、リソース一覧から「restfulapiforevernote-aws-function-00」をクリック。
![image.png](https://qiita-image-store.s3.ap-northeast-1.amazonaws.com/0/258416/99f6cf38-4551-1859-eb1b-0653796c7081.png)

Lambda関数「restfulapiforevernote-aws-function-00」のページが開くので、[環境変数]でキー「en_access_token」の値に先程取得したアクセストークンを入力。
![image.png](https://qiita-image-store.s3.ap-northeast-1.amazonaws.com/0/258416/443e52b1-21d4-645b-1626-7bad39d4ed23.png)

[暗号化の設定]で「伝送中の暗号化のためのヘルパーの有効化」にチェックを入れ、「保管時に暗号化する AWS KMS キー」と同じAWS KMSキーを選択し、「en_access_token」の[暗号化]をクリック。
![image.png](https://qiita-image-store.s3.ap-northeast-1.amazonaws.com/0/258416/445be758-b575-c215-e5ba-34e34c343520.png)

アクセストークンが画面上で暗号化されたら[保存]をクリック。
![image.png](https://qiita-image-store.s3.ap-northeast-1.amazonaws.com/0/258416/90036553-0eea-0c8b-c387-d0276b245682.png)

# API リファレンス

Restful API「restfulapiforevernote」の利用方法を記載する。

## ホスト

ブラウザから[API Gatewayのコンソール](https://ap-northeast-1.console.aws.amazon.com/apigateway/home)を開く。

[restfulapiforevernote-aws-api-00] -> [ステージ] -> [dev] -> [devステージエディター]よりAPIのURLを確認する。
このURLがAPI Gateway経由でRESTでアクセスする際のホストとなる。
![image.png](https://qiita-image-store.s3.ap-northeast-1.amazonaws.com/0/258416/1bdb0e16-4126-ed09-8def-752726f6d4b0.png)

## 認証

### API Gateway経由でリクエストする場合

ブラウザから[API Gatewayのコンソール](https://ap-northeast-1.console.aws.amazon.com/apigateway/home)を開く。

[APIキー] -> [restfulapiforevernote-aws-apikey-00]より[表示]をクリックしてAPIキーを確認する。
このAPIキーをAPI Gateway経由でRESTでアクセスする際にリクエストヘッダの`x-api-key`に指定する。
![image.png](https://qiita-image-store.s3.ap-northeast-1.amazonaws.com/0/258416/4b2eafde-8123-2a62-9960-d747eea2b202.png)

### boto3でリクエストする場合

ほかのLambda関数よりboto3でリクエストする場合は、Lambdaの実行ロールに最低でも以下の権限を持つポリシーをアタッチする。

```Statement.json
{
    "Action": [
        "lambda:InvokeFunction"
    ],
    "Resource": "*",
    "Effect": "Allow"
}
```

## 各種API

### ノート検索・複数取得

```
GET /notes
```

#### リクエストパラメータ

| パラメータ | 内容 | 型 | 必須 | デフォルト値 |
| --- | --- | --- | :---: | :---: |
| q | ノート検索キーワード | String | no | キーワードによるフィルターなし |
| notebookguid | ノートを検索するノートブックのGUID | String | no |  |
| searchtype | ノートの検索タイプ（0:タイトル部分一致/1:タイトル完全一致/2:タイトル・本文部分一致） | Integer（0/1/2） | no | 0 |
| includecontent | レスポンスにノートのコンテンツを含めるかどうか（0:含めない/1:含める） | Integer（0/1） | no | 0 |
| maxnotescount | レスポンスするノートの最大数 | Integer | no  | 1 |

#### 利用例1 指定のノートブックからタイトルで検索

##### cURLからリクエストする場合

```sh
curl -H 'x-api-key: ＜APIキー＞' \
  "＜ホスト＞/notes?q=notetitle1&notebookguid=xxxxxxxx-9780-4e32-a99f-9e97cc98d299&searchtype=1&includecontent=1"
```

##### ほかのLambda関数(Python)からリクエストする場合

```py
import boto3,json

def lambda_handler(event, context):

    input_event = {
        "httpMethod": "GET",
        "resource": "/notes",
        "queryStringParameters": {
            "q": "notetitle1",
            "notebookguid": "＜ノートブックGUID＞",
            "searchtype": 1,
            "includecontent": 1
        }
    }
    Payload = json.dumps(input_event)
    
    response = boto3.client('lambda').invoke(
        FunctionName='restfulever-aws-function-00',
        InvocationType='RequestResponse',
        Payload=Payload
    )

    data = json.loads(json.loads(response['Payload'].read())['body'])
    print(json.dumps(data, indent=2))
```

##### レスポンス例

```json
[
  {
    "noteguid": "xxxxxxxx-5650-407e-a075-f7299a2d425a",
    "title": "notetitle1",
    "notebookguid": "xxxxxxxx-9780-4e32-a99f-9e97cc98d299",
    "content": "<div>ノートの本文ですよ</div>"
  }
]
```

#### 利用例2 最新のノートを３つ取得

##### cURLからリクエストする場合

```sh
curl -H 'x-api-key: ＜APIキー＞' \
  "＜ホスト＞/notes?maxnotescount=3"
```

##### ほかのLambda関数(Python)からリクエストする場合

```py
import boto3,json

def lambda_handler(event, context):

    input_event = {
        "httpMethod": "GET",
        "resource": "/notes",
        "queryStringParameters": {
            "maxnotescount": 3
        }
    }
    Payload = json.dumps(input_event)
    
    response = boto3.client('lambda').invoke(
        FunctionName='restfulever-aws-function-00',
        InvocationType='RequestResponse',
        Payload=Payload
    )

    data = json.loads(json.loads(response['Payload'].read())['body'])
    print(json.dumps(data, indent=2))
```

##### レスポンス例

```json
[
  {
    "noteguid": "xxxxxxxx-5650-407e-a075-f7299a2d425a",
    "title": "notetitle1",
    "notebookguid": "xxxxxxxx-9780-4e32-a99f-9e97cc98d299"
  },
  {
    "noteguid": "xxxxxxxx-0d68-422f-8846-48174f1f6611",
    "title": "FW: 配達完了:ご注文商品の配達が完了しました。",
    "notebookguid": "xxxxxxxx-1b01-439b-994b-9b42640ad29a"
  },
  {
    "noteguid": "xxxxxxxx-cef7-4028-a0f8-bdec52448db6",
    "title": "2019/08/12 ラジオ_視聴",
    "notebookguid": "xxxxxxxx-4d79-472d-8ba5-e7db3e354ac9"
  }
]
```

### ノート個別取得

```
GET /note/:noteguid
```

#### 利用例

##### cURLからリクエストする場合

```sh
curl -H 'x-api-key: ＜APIキー＞' \
  "＜ホスト＞/note/xxxxxxxx-5650-407e-a075-f7299a2d425a"
```

##### ほかのLambda関数(Python)からリクエストする場合

```py
import boto3,json

def lambda_handler(event, context):

    pathParameters = {
        "noteguid": "xxxxxxxx-5650-407e-a075-f7299a2d425a"
    }

    input_event = {
        "httpMethod": "GET",
        "resource": "/note/{noteguid}",
        "pathParameters": json.dumps(pathParameters)
    }
    Payload = json.dumps(input_event)

    response = boto3.client('lambda').invoke(
        FunctionName='restfulever-aws-function-00',
        InvocationType='RequestResponse',
        Payload=Payload
    )

    data = json.loads(json.loads(response['Payload'].read())['body'])
    print(json.dumps(data, indent=2))
```

##### レスポンス例

```json
{
  "noteguid": "xxxxxxxx-5650-407e-a075-f7299a2d425a",
  "title": "notetitle1",
  "notebookguid": "xxxxxxxx-9780-4e32-a99f-9e97cc98d299",
  "content": "<div>ノートの本文ですよ</div>"
}
```

### ノート作成

```
POST /note
```

#### データ

| JSONキー | 内容 | 型 | 必須 | デフォルト値 |
| --- | --- | :---: | :---: | :---: |
| notetitle | ノートタイトル | String | yes | |
| notecontent | ノート本文 | String | no | |
| notebookguid | 作成先ノートブックGUID | String | no | 既定のノートブック |
| resource_ary | 添付ファイル情報の配列（詳細は「ほかのLambda関数(Python)からリクエストする場合」を参照） | Array | no |  |

#### 利用例

##### cURLからリクエストする場合

```添付なしのノートを作成する場合.sh
curl -H 'x-api-key: ＜APIキー＞' \
  -d '{"notetitle":"notetitle2","notecontent":"本文ですよ。","notebookguid":"xxxxxxxx-9780-4e32-a99f-bc9737c0afc6"}' \
  "＜ホスト＞/note"
```

##### ほかのLambda関数(Python)からリクエストする場合

```画像を添付して本文中に表示するノートを作成する場合.py
import json,hashlib,base64,boto3
import urllib.request

def lambda_handler(event, context):

    resources = []
    hexhash = ''
    imgurl_ary = [
        'https://www.contoso.com/image1.png',
        'https://www.contoso.com/image2.png'
    ]

    for item in imgurl_ary:
        bodybinary = urllib.request.urlopen(item).read()
        bodyhash = hashlib.md5(bodybinary).hexdigest()
        resource = {
            'databody' : str(base64.b64encode(bodybinary).decode()),
            'bodyhash' : bodyhash,
            'mimetype' : 'image/png'
        }
        hexhash += '<br /><en-media type="%s" hash="%s" />' % ('image/png',bodyhash)
        resources.append(resource)

    body = {
        'notetitle' : 'notetitle2',
        'notecontent' : '画像を添付して本文に表示してみた。' + '<br />' + hexhash,
        'resource_ary' : resources
    }

    input_event = {
        "httpMethod": "POST",
        "resource": "/note",
        "body": json.dumps(body)
    }

    response = boto3.client('lambda').invoke(
        FunctionName = 'restfulever-aws-function-00',
        InvocationType = 'RequestResponse',
        Payload = json.dumps(input_event)
    )

    data = json.loads(json.loads(response['Payload'].read())['body'])
    print(json.dumps(data, indent=2))
```

##### レスポンス例

```json
{
  "noteguid": "xxxxxxxx-456f-4d17-b6f9-9e97cc98d299",
  "title": "notetitle2",
  "notebookguid": "xxxxxxxx-9780-4e32-a99f-bc9737c0afc6"
}
```

##### 「ほかのLambda関数(Python)からリクエストする場合」で作成したノート例

<img src="https://qiita-image-store.s3.ap-northeast-1.amazonaws.com/0/258416/56eb2ad1-f6eb-f3e4-1c2d-c01a6477bf59.png" width="300px">

### ノート更新

```
PATCH /note/:noteguid
```

#### データ

| JSONキー | 内容 | 型 | 必須 | デフォルト値 |
| --- | --- | :---: | :---: | --- |
| notetitle | ノートタイトル | String | yes | |
| notecontent | ノート本文 | String | no | キーを指定しない場合はノート本文の更新なし |
| notebookguid | 作成先ノートブックGUID | String | no | キーを指定しない場合はノートブックの移動なし |

#### 利用例

##### cURLからリクエストする場合

```sh
curl -X PATCH -H 'x-api-key: ＜APIキー＞' \
  -d '{"notetitle":"notetitle3","notecontent":"本文を更新してみる","notebookguid":"xxxxxxxx-1b01-439b-994b-a48174f1f6611"}' \
  "＜ホスト＞/note/xxxxxxxx-5650-407e-a075-9b42640ad29"
```

##### ほかのLambda関数(Python)からリクエストする場合

```py
import json,boto3

def lambda_handler(event, context):

    body = {
        "notebookguid": "xxxxxxxx-1b01-439b-994b-a48174f1f6611",
        "notetitle": "notetitle3",
        "notecontent": "本文を更新してみる"
    }

    pathParameters = {
        "noteguid": "xxxxxxxx-5650-407e-a075-9b42640ad29"
    }

    input_event = {
        "httpMethod": "PATCH",
        "resource": "/note/{noteguid}",
        "pathParameters": json.dumps(pathParameters),
        "body": json.dumps(body)
    }
    Payload = json.dumps(input_event)

    response = boto3.client('lambda').invoke(
        FunctionName='restfulever-aws-function-00',
        InvocationType='RequestResponse',
        Payload=Payload
    )

    data = json.loads(json.loads(response['Payload'].read())['body'])
    print(json.dumps(data, indent=2))
```

##### レスポンス例

```json
{
  "noteguid": "xxxxxxxx-5650-407e-a075-9b42640ad29",
  "title": "notetitle3",
  "notebookguid": "xxxxxxxx-1b01-439b-994b-a48174f1f6611"
}
```

### ノート削除

未実装

# 参考

http://dev.evernote.com/doc/reference/
http://wararyo.blogspot.com/2015/07/evernote-python.html
https://qiita.com/r-wakatsuki/items/4076e3b8032d06f85aea

以上