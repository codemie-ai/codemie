# Provider Client

## Generation

We use OpenAPI Generator

1. Install via `brew install openapi-generator` or [other methods](https://openapi-generator.tech/docs/installation)
2. Update `codemie.spi.json` and `codemei.spi.schema.json` if needed
3. Remove the ./client folder (*Optional*) 
4. Run inside this folder:
```bash
openapi-generator generate -i epam_ai_run.spi.json -g python  -o ./../../../  --additional-properties=generateSourceCodeOnly=false,packageName=codemie.clients.provider.client,apiTests=false,modelTests=false
````

## Usage Example
```python
from codemie.clients.provider import client as provider_client

def call_api():
    config = provider_client.Configuration(
        host="http://super-provider.com/api/"
    )

    with provider_client.ApiClient(config) as api_client:
        api_instance = provider_client.ServiceProviderMetadataHealthApi(api_client)
        x_correlation_id = 'x_correlation_id_example'
        api_response = api_instance.health_check(x_correlation_id=x_correlation_id)
        return api_response
```