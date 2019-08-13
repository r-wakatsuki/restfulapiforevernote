FROM python:3.7
WORKDIR /work

CMD apt update && \
    apt install -y zip && \
    mkdir python && \
    pip install -t ./python evernote3 oauth2 && \
    zip -r ./zipdir/layer.zip python