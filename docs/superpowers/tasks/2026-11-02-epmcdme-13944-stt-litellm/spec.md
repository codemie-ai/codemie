
  ⭐ Story  🚧 Ready for Review  ⌛ Mon, 02 Nov 26  👷 Tair Mustafaiev  🔑️ EPMCDME-13944  💭 4 comments  🧵 0 linked 
                                                                                                                      
  # Implement generic Speech-to-Text support via LiteLLM-first routing                                                
                                                                                                                      
  ⏱️  Wed, 05 Aug 26  🔎 Tair Mustafaiev  🚀 Major  📦 None  🏷️  AI-Generated, AI/Run, codemie_feedback  👀 1 watchers
                                                                                                                      
  ------------------------ Description ------------------------                                                       
                                                                                                                      
  ## Summary                                                                                                          
                                                                                                                      
  Implement generic Speech-to-Text (STT) support in CodeMie with LiteLLM. Today CodeMie supports one STT model that is
  configured with environment variables and calls it directly. The new implementation keeps that model and adds the   
  STT models that are onboarded in LiteLLM. CodeMie sends transcription requests to the OpenAI-compatible             
  {{/audio/transcriptions}} endpoint, shows the STT calls in the observability tools, and passes configurable provider
  metadata (headers) between the portal, the AI Gateway and the provider.                                             
                                                                                                                      
  ## Description                                                                                                      
                                                                                                                      
  As a CodeMie deployment engineer or platform administrator, I want CodeMie to use the STT models that exist in      
  LiteLLM, so that CodeMie can support client-specific STT providers without a separate CodeMie configuration for each
  model.                                                                                                              
                                                                                                                      
  • STT models in LiteLLM (mode {{audio_transcription}}) are found automatically and added to the list of STT models. 
  • The STT model that is configured with the environment variables {{STT_API_*}} (for example OpenAI Whisper) stays  
  supported and stays the default. Existing deployments need no change.                                               
  • A deployment that has only LiteLLM STT models works without the environment variables.                            
  • A client can choose the model and the language for a request. The existing voice recording flow in the UI does not
  need to change.                                                                                                     
  • The language and the header metadata for a provider are configured per model in LiteLLM.                          
  • Each STT call is traced in Langfuse (and in Phoenix when Phoenix is the active provider).                         
                                                                                                                      
  Limitations of the current implementation that this ticket removes:                                                 
                                                                                                                      
  • The STT client is hardcoded to Azure OpenAI.                                                                      
  • Only one STT model can be used. LiteLLM models cannot be used.                                                    
  • The transcription call does not support the {{language}} parameter.                                               
  • The {{/speech-recognition}} endpoint does not accept a model or a language.                                       
  • STT calls are not shown in the observability tools.                                                               
  • Provider headers cannot be passed on or returned.                                                                 
                                                                                                                      
  ## Preconditions                                                                                                    
                                                                                                                      
  • CodeMie backend has the existing {{/v1/speech-recognition}} endpoint.                                             
  • CodeMie UI already records audio and sends it to the backend as a multipart file.                                 
  • LiteLLM exposes the STT model through the OpenAI-compatible {{/audio/transcriptions}} endpoint. The model has     
  {{model_info.mode: audio_transcription}}.                                                                           
  • Existing deployments can still use the current STT environment variables:                                         
      • {{STT_API_URL}}                                                                                               
      • {{STT_API_KEY}}                                                                                               
      • {{STT_API_DEPLOYMENT_NAME}}                                                                                   
      • {{STT_MODEL_NAME}}                                                                                            
                                                                                                                      
                                                                                                                      
  ## Scenarios of Use                                                                                                 
                                                                                                                      
  • *Existing deployment (backward compatible)*                                                                       
      • The deployment has only the {{STT_API_*}} variables, for example for OpenAI Whisper.                          
      • The user records a voice message. CodeMie calls the model as before.                                          
      • The model is listed in {{GET /v1/audio_transcription_models}} and it is the default.                          
  • *LiteLLM models only*                                                                                             
      • The deployment has no {{STT_API_*}} variables. LiteLLM has one or more models with the mode                   
      {{audio_transcription}}.                                                                                        
      • CodeMie lists only the LiteLLM models. The first one is the default.                                          
      • CodeMie sends the audio to LiteLLM and returns the transcribed text to the chat input.                        
  • *Environment model and LiteLLM models together*                                                                   
      • The deployment has the {{STT_API_*}} variables and LiteLLM audio models.                                      
      • CodeMie lists the environment model first and then the LiteLLM models. The environment model stays the default.
      • A request can choose any listed model with the {{model}} parameter.                                           
  • *Language*                                                                                                        
      • The client can send the optional {{language}} parameter.                                                      
      • CodeMie uses the request language. If the request has none, CodeMie uses the {{default_language}} of the model.
      If both are empty, CodeMie sends no language and the model detects it.                                          
  • *Provider metadata*                                                                                               
      • The LiteLLM model has {{stt_config}} in its {{model_info}}.                                                   
      • CodeMie sends the allowed request headers to the AI Gateway and returns the allowed provider response headers 
      and fields to the caller.                                                                                       
  • *Observability*                                                                                                   
      • An STT request creates a trace in Langfuse with the model, the language, the transcript, the usage and the    
      provider metadata.                                                                                              
                                                                                                                      
                                                                                                                      
  ## Affected Areas                                                                                                   
                                                                                                                      
  • CodeMie backend STT implementation (new {{SttService}})                                                           
  • CodeMie backend LLM service and LiteLLM model discovery                                                           
  • {{/v1/speech-recognition}} API endpoint and the new {{GET /v1/audio_transcription_models}} endpoint               
  • LiteLLM integration path for STT                                                                                  
  • Observability provider interface (Langfuse and Phoenix)                                                           
  • Existing voice recording flow in CodeMie UI                                                                       
  • LiteLLM proxy configuration of the STT models                                                                     
                                                                                                                      
  ## Acceptance Criteria                                                                                              
                                                                                                                      
  • STT models in LiteLLM with {{model_info.mode: audio_transcription}} are found automatically. They are not shown as
  chat models.                                                                                                        
  • A deployment with only the {{STT_API_*}} variables behaves as before. The environment model is listed and it is   
  the default. The call to the model does not change.                                                                 
  • A deployment with LiteLLM audio models and the {{STT_API_*}} variables lists the environment model first and then 
  the LiteLLM models. The environment model stays the default.                                                        
  • A deployment with LiteLLM audio models and no {{STT_API_*}} variables lists only the LiteLLM models. The first one
  is the default.                                                                                                     
  • If a LiteLLM model has the same name as the environment model, the LiteLLM model is used.                         
  • A new service {{SttService}} is responsible for:                                                                  
      • choosing the STT model                                                                                        
      • creating the OpenAI-compatible client (LiteLLM client for LiteLLM models, the existing Azure client for the   
      environment model)                                                                                              
      • applying the language and the provider headers                                                                
      • returning the transcribed text and the provider metadata                                                      
  • LiteLLM requests use the same credentials, tagging headers and budget attribution as the other LiteLLM calls of   
  CodeMie.                                                                                                            
  • {{POST /v1/speech-recognition}} accepts the optional form fields {{model}} and {{language}}. An unknown model     
  returns HTTP 400 with the reason.                                                                                   
  • The audio file name has the correct extension for the real audio format (for example {{audio.webm}}). The default 
  is {{audio.wav}}.                                                                                                   
  • {{GET /v1/audio_transcription_models}} returns the merged list of models. {{stt_support}} in {{GET /v1/user/data}}
  is true when at least one STT model is available.                                                                   
  • The language is chosen in this order: the request {{language}}, then the {{default_language}} of the model, then  
  no language parameter. The model from the {{STT_API_*}} variables has no model language, so it gets a language only 
  from the request.                                                                                                   
  • The existing CodeMie UI voice recording flow works without mandatory UI changes.                                  
  • The guide {{.ai-run/guides/integration/llm-providers.md}} describes the model sources and the metadata key.       
  • The Observability criteria and the Provider Metadata criteria below are met.                                      
  • Unit or integration tests cover:                                                                                  
      • the model list for the three deployment cases                                                                 
      • the choice of the default model and the choice of a named model                                               
      • LiteLLM routing and routing of the environment model                                                          
      • the language choice                                                                                           
      • an unknown model                                                                                              
      • the header forwarding and the response metadata                                                               
      • the observability calls                                                                                       
  • No regression is introduced for existing deployments that use the current single STT model.                       
                                                                                                                      
  ## Acceptance Criteria (Observability and Provider Metadata)                                                        
                                                                                                                      
  • An STT request creates a Langfuse trace {{Audio Transcription}} with a generation {{stt.transcribe}} that shows   
  the model, the language, the transcript, and the usage when it is available.                                        
  • A failed STT call is shown in Langfuse as an error with the error text.                                           
  • The audio content is not stored in the observability tool.                                                        
  • The same data is recorded when Phoenix is the active provider. STT works when observability is disabled.          
  • Request headers that match {{forward_request_headers}} of the model reach the AI Gateway. Other headers, including
  {{x-userinfo}} and {{Authorization}}, are not sent.                                                                 
  • Response headers that match {{response_headers}} and body fields that match {{response_body_fields}} are returned 
  to the caller. They are also shown as metadata on the Langfuse generation and trace, together with the forwarded    
  request headers.                                                                                                    
  • Provider errors are mapped to a clear HTTP status: 400 (invalid request), 413 (file too large), 429 (rate limit), 
  503 (provider unavailable), 504 (timeout) and 502 (other provider or gateway errors), with the provider message in  
  the details.                                                                                                        
  • No provider-specific name is hardcoded. A different provider needs only a change of the model configuration in    
  LiteLLM.                                                                                                            
                                                                                                                      
  ## STT Model Sources and Backward Compatibility                                                                     
                                                                                                                      
  • The model that is configured with the environment variables {{STT_API_*}} (for example OpenAI Whisper) keeps      
  working in the same way as before. It is always listed first and it is the default model.                           
  • When LiteLLM is on, the LiteLLM models with {{model_info.mode: audio_transcription}} are added after it. CodeMie  
  reads them from the LiteLLM model list. No CodeMie configuration file is needed.                                    
  • If no environment model is configured, only the LiteLLM models are listed. The first one is the default.          
  • If a LiteLLM model has the same name as the environment model, the LiteLLM model is used.                         
  • {{GET /v1/audio_transcription_models}} returns the merged list. {{POST /v1/speech-recognition}} accepts the       
  optional form fields {{model}} and {{language}}.                                                                    
  • The mode {{realtime}} is not supported. It is for streaming over a websocket. A model for file transcription must 
  have the mode {{audio_transcription}} in LiteLLM.                                                                   
                                                                                                                      
  ## Observability                                                                                                    
                                                                                                                      
  STT calls must appear in the observability tools in the same way as chat and agent calls. Langfuse is the main      
  target. Phoenix is supported when it is the active provider.                                                        
                                                                                                                      
  • Each STT request creates a trace named {{Audio Transcription}}. The trace has one generation observation named    
  {{stt.transcribe}}.                                                                                                 
  • The generation records the model, the language, the audio file name and size, the transcript, the usage that the  
  provider returns (tokens or audio seconds), and the error text when the call fails.                                 
  • The trace records the CodeMie user ID, the tag {{stt}}, the transcript as output, and the provider metadata that  
  is described below.                                                                                                 
  • The audio content is never sent to the observability tool.                                                        
  • The code is independent of the observability provider. A new method {{update_current_generation}} is added to the 
  observability provider interface. The Langfuse and Phoenix providers implement it. The no-op provider ignores it.   
  • If observability is disabled, STT works as before.                                                                
                                                                                                                      
  ## Provider Metadata (Headers)                                                                                      
                                                                                                                      
  Target architecture: Citizen Portal -> CodeMie -> AI Gateway (LiteLLM) -> STT provider -> file storage.             
                                                                                                                      
  Request direction:                                                                                                  
                                                                                                                      
  • The portal sends the audio file and the headers {{x-userinfo}}, {{x-conversation-id}} and {{x-conversation-trace- 
  id}} to CodeMie.                                                                                                    
  • CodeMie sends the audio file and the headers {{x-conversation-id}} and {{x-conversation-trace-id}} to the AI      
  Gateway. CodeMie does not send {{x-userinfo}}.                                                                      
                                                                                                                      
  Response direction:                                                                                                 
                                                                                                                      
  • The provider returns the standard OpenAI body (the transcribed text and the usage) and provider-specific headers. 
  For example, {{x-transaction-id}} is the ID of the transcription, and {{x-file-uri}} is the URL of the stored       
  recording. The provider sends the second header only when it keeps the audio. The AI Gateway relays the headers to  
  CodeMie.                                                                                                            
  • CodeMie returns the transaction ID and the storage URL (when present) to the portal, together with the transcribed
  text.                                                                                                               
  • CodeMie writes the transaction ID, the storage URL, the conversation ID, the conversation trace ID and the        
  transcribed text to Langfuse.                                                                                       
  • The provider can store {{x-conversation-id}} and {{x-conversation-trace-id}} with the transcription, and it can   
  limit their length.                                                                                                 
                                                                                                                      
  The metadata is configured per model, not coded. No provider name is used in the code. The configuration is the key 
  {{stt_config}} in the {{model_info}} of the model in the LiteLLM proxy configuration (config.yaml). It is not a     
  CodeMie environment variable. It applies only to LiteLLM models. The model from the {{STT_API_**}} variables has no 
  metadata. The key has one language and three lists. A name is matched without regard to case. A trailing {{**}}     
  matches a prefix. All three lists are empty by default.                                                             
                                                                                                                      
  • {{default_language}}: the language for this model when the request has none. Example: {{de}}.                     
  • {{forward_request_headers}}: incoming CodeMie request headers that are sent to the AI Gateway. Example: {{["x-    
  conversation-id", "x-conversation-trace-id"]}}.                                                                     
  • {{response_headers}}: provider response headers that are returned to the caller and recorded in observability.    
  Example: {{["x-transaction-id", "x-file-uri"]}}. A prefix pattern such as {{x-provider-*}} matches only the names   
  that start with that prefix. LiteLLM relays provider headers with the prefix {{llm_provider-}}. CodeMie removes this
  prefix before it matches the names.                                                                                 
  • {{response_body_fields}}: extra top-level fields of the provider response body that are returned to the caller and
  recorded in observability. Use it only when the provider adds fields to the body. Most providers return only the    
  OpenAI fields.                                                                                                      
                                                                                                                      
  API change: {{POST /v1/speech-recognition}} keeps the transcript in {{message}}. The matched response headers are   
  set                                                                                                                 
  as HTTP response headers. The matched headers and body fields are also returned in a new field {{data}}.            
                                                                                                                      
  ## Open Points                                                                                                      
                                                                                                                      
  • The AI Gateway must be set to forward client headers to the provider. This is a LiteLLM setting. Confirm it for   
  the target environment.                                                                                             
  • Confirm with the real LiteLLM instance that the provider response headers reach CodeMie (LiteLLM relays them as   
  {{llm_provider-}}) and that the request headers reach the provider.                                                 
  • A provider can accept only the OpenAI fields. CodeMie sends the LiteLLM customer ID as an extra field {{user}} for
  budget tracking. Confirm that LiteLLM does not pass this field to the provider.                                     
  • A provider can support only some languages. Set {{default_language}} in the {{stt_config}} of that model. The     
  limits of the provider (file size, audio length, timeout) apply, and the errors are mapped to HTTP statuses.        
  • A LiteLLM model for file transcription must have the mode {{audio_transcription}}. A model with the mode          
  {{realtime}} is not listed.                                                                                         
  • The OpenAPI baseline of the router guard tests must be regenerated after the API change.                          
                                                                                                                      
  ------------------------ 4 Comments ------------------------                                                        
                                                                                                                      
  Auto EPMD-EDP AIAssistant • Wed, 05 Aug 26 • Latest comment                                                         
                                                                                                                      
  📊 Actual complexity (post-implementation): M — 19/36 (initial estimate was L — 21/36).                             
                                                                                                                      
  Delta: Technical Risk L→M and Affected Layers L→M. The 4-level language resolution turned out to be a single or     
  chain; LiteLLM-first / Azure-fallback routing reused existing openai.OpenAI / AzureOpenAI clients rather than a new 
  integration; only API + Service + Config layers changed, no persistence/schema change. Realtime mode deferred behind
  a guard, not built.                                                                                                 
                                                                                                                      
  Implementation: ~10 files (7 modified, 3 new incl. 2 test files), ~600 impl lines, 26 new tests passing. Net        
  simplification — the earlier parallel config/audio/ tree + audio_config.py were removed in favor of inlining        
  STTModel/stt_models into the existing LLMConfig YAML pattern. QA gates: lint / license / build PASS; STT tests 26/26
  PASS. Implementation committed.                                                                                     
                                                                                                                      
[38;5;242m[0m[38;5;242m[0m  [38;5;242mUse --comments  with jira issue view to load more comments[m                                                          [0m
                                                                                                                      
[38;5;242m[0m[38;5;242m[0m  [38;5;242mView this issue on Jira: https://jiraeu.epam.com/browse/EPMCDME-13944[m                                               [0m

