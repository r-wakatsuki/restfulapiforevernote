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