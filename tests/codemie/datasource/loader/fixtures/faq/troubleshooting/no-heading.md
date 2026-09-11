docker compose ps shows healthy but the backend logs report connection refused to Elasticsearch.

Check the actual cluster health: curl localhost:9200/_cluster/health. A healthy container does not
guarantee a green cluster — disk watermarks can block writes while the healthcheck still passes.
