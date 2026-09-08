FROM fork-branch:latest
COPY video/fixture/index.html /opt/multiverse-demo/index.html
COPY --chmod=755 video/fixture/start.sh /opt/multiverse-demo/start.sh
ENTRYPOINT ["/opt/multiverse-demo/start.sh"]
