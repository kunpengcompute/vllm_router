# SPDX-License-Identifier: Apache-2.0

# Adapted from
# https://github.com/lm-sys/FastChat/blob/168ccc29d3f7edc50823016105c024fe2282732a/fastchat/protocol/openai_api_protocol.py

import importlib
import os
import re
from argparse import Namespace
from pathlib import Path

from typing import Optional, Union, Annotated, ClassVar, Literal, Any, List
from urllib.parse import urlparse

from pydantic import BaseModel, model_validator, ConfigDict, Field, field_validator
from pydantic_core import Url

from utils.logger import logger

_LONG_INFO = Namespace(min=-9223372036854775808, max=9223372036854775807)


class LogitsProcessorConstructor(BaseModel):
    qualname: str
    args: Optional[list[Any]] = None
    kwargs: Optional[dict[str, Any]] = None


LogitsProcessors = list[Union[str, LogitsProcessorConstructor]]


class OpenAIBaseModel(BaseModel):
    # OpenAI API does allow extra fields
    model_config = ConfigDict(extra="allow")

    # Cache class field names
    field_names: ClassVar[Optional[set[str]]] = None

    @model_validator(mode="wrap")
    @classmethod
    def __log_extra_fields__(cls, data, handler):
        result = handler(data)
        if not isinstance(data, dict):
            return result        
        field_names = cls.field_names        
        if field_names is None:                                                                                    
            # Get all class field names and their potential aliases
            field_names = set()
            for field_name, field in cls.model_fields.items():
                field_names.add(field_name)
                if alias := getattr(field, 'alias', None):
                    field_names.add(alias)
            cls.field_names = field_names
        
        # Compare against both field names and aliases
        if any(k not in field_names for k in data):
            logger.warning(
                "The following fields were present in the request "
                "but ignored: %s", 
                data.keys() - field_names)
        return result


class StreamOptions(OpenAIBaseModel):
    include_usage: Optional[bool] = True
    continuous_usage_stats: Optional[bool] = False


class JsonSchemaResponseFormat(OpenAIBaseModel):
    name: str    
    description: Optional[str] = None    
    # schema is the field in openai but that causes conflicts with pydantic so
    # instead use json_schema with an alias
    json_schema: Optional[dict[str, Any]] = Field(default=None, alias='schema')
    strict: Optional[bool] = None


class ResponseFormat(OpenAIBaseModel):
    # type must be "json_schema", "json_object" or "text"
    type: Literal["text", "json_object", "json_schema"]
    json_schema: Optional[JsonSchemaResponseFormat] = None


class CompletionRequest(OpenAIBaseModel):
    # Ordered by official OpenAI API documentation
    # https://platform.openai.com/docs/api-reference/completions/create
    model: Optional[str] = None    
    prompt: Union[list[int], list[list[int]], str, list[str]]
    best_of: Optional[int] = None    
    echo: Optional[bool] = False    
    frequency_penalty: Optional[float] = 0.0
    logit_bias: Optional[dict[str, float]] = None    
    logprobs: Optional[int] = None    
    max_tokens: Optional[int] = 16
    n: int = 1
    presence_penalty: Optional[float] = 0.0
    seed: Optional[int] = Field(None, ge=_LONG_INFO.min, le=_LONG_INFO.max)
    stop: Optional[Union[str, list[str]]] = Field(default_factory=list)
    stream: Optional[bool] = False    
    stream_options: Optional[StreamOptions] = None    
    suffix: Optional[str] = None    
    temperature: Optional[float] = None    
    top_p: Optional[float] = None    
    user: Optional[str] = None

    # doc: begin-completion-sampling-params
    use_beam_search: bool = False    
    top_k: Optional[int] = None    
    min_p: Optional[float] = None    
    repetition_penalty: Optional[float] = None    
    length_penalty: float = 1.0
    stop_token_ids: Optional[list[int]] = Field(default_factory=list)
    include_stop_str_in_output: bool = False    
    ignore_eos: bool = False    
    min_tokens: int = 0
    skip_special_tokens: bool = True    
    spaces_between_special_tokens: bool = True    
    truncate_prompt_tokens: Optional[Annotated[int, Field(ge=1)]] = None    
    allowed_token_ids: Optional[list[int]] = None    
    prompt_logprobs: Optional[int] = None    
    # doc: end-completion-sampling-params

    # doc: begin-completion-extra-params
    add_special_tokens: bool = Field(
        default=True,
        description=(
            "If true (the default), special tokens (e.g. BOS) will be added to "
            "the prompt."),
    )
    response_format: Optional[ResponseFormat] = Field(
        default=None,
        description=(
            "Similar to chat completion, this parameter specifies the format of "
            "output. Only {'type': 'json_object'}, {'type': 'json_schema'} or "
            "{'type': 'text' } is supported."),
    )
    guided_json: Optional[Union[str, dict, BaseModel]] = Field(
        default=None,
        description="If specified, the output will follow the JSON schema.",
    )
    guided_regex: Optional[str] = Field(
        default=None,
        description=(
            "If specified, the output will follow the regex pattern."),
    )
    guided_choice: Optional[list[str]] = Field(
        default=None,
        description=(
            "If specified, the output will be exactly one of the choices."),
    )
    guided_grammar: Optional[str] = Field(
        default=None,
        description=(
        "If specified, the output will follow the context free grammar."),
    )
    guided_decoding_backend: Optional[str] = Field(
        default=None,
        description=(
            "If specified, will override the default guided decoding backend "
            "of the server for this specific request. If set, must be one of "
            "'outlines' / 'lm-format-enforcer'")
    )
    guided_whitespace_pattern: Optional[str] = Field(
        default=None,
        description=(
            "If specified, will override the default whitespace pattern "
            "for guided json decoding.")
    )
    priority: int = Field(
        default=0,
        description=(
            "The priority of the request (lower means earlier handling; "
            "default: 0). Any priority other than 0 will raise an error "
            "if the served model does not use priority scheduling.")
    )
    logits_processors: Optional[LogitsProcessors] = Field(
    default=None,
    description=(
        "A list of either qualified names of logits processors, or "
        "constructor objects, to apply when sampling. A constructor is "
        "a JSON object with a required 'qualname' field specifying the "
        "qualified name of the processor class/factory, and optional "
        "'args' and 'kwargs' fields containing positional and keyword "
        "arguments. For example: {'qualname': "
        "'my_module.MyLogitsProcessor', 'args': [1, 2], 'kwargs': "
        "{'param': 'value'}}."))
    return_tokens_as_token_ids: Optional[bool] = Field(
        default=None,
        description=(
            "If specified with 'logprobs', tokens are represented "
            " as strings of the form 'token_id:{token_id}' so that tokens "
            "that are not JSON-encodable can be identified.")
    )

    # doc: end-completion-extra-params

    # Default sampling parameters for completion requests
    _DEFAULT_SAMPLING_PARAMS: dict = {
        "repetition_penalty": 1.0,
        "temperature": 1.0,
        "top_p": 1.0,
        "top_k": -1,
        "min_p": 0.0,
    }

    @model_validator(mode="before")
    @classmethod    
    def check_guided_decoding_count(cls, data):
        guide_count = sum([
            "guided_json" in data and data["guided_json"] is not None,
            "guided_regex" in data and data["guided_regex"] is not None,
            "guided_choice" in data and data["guided_choice"] is not None
        ])
        if guide_count > 1:
            raise ValueError(
                "You can only use one kind of guided decoding "
                "('guided_json', 'guided_regex' or 'guided_choice').")
        return data

        @model_validator(mode="before")
        @classmethod    
        def check_logprobs(cls, data):
            if (prompt_logprobs := data.get("prompt_logprobs")) is not None:
                if data.get("stream") and prompt_logprobs > 0:
                    raise ValueError(
                        "`prompt_logprobs` are not available when `stream=True`.")

                if prompt_logprobs < 0:
                    raise ValueError("`prompt_logprobs` must be a positive value.")

            if (logprobs := data.get("logprobs")) is not None and logprobs < 0:
                raise ValueError("`logprobs` must be a positive value.")

            return data
           
        @model_validator(mode="before")
        @classmethod    
        def validate_stream_options(cls, data):
            if data.get("stream_options") and not data.get("stream"):
                raise ValueError(
                    "Stream options can only be defined when `stream=True`.")

            return data


class WorkUrls(BaseModel):
    urls: Union[str, List[str]]

    @field_validator("urls")
    @classmethod    
    def check_urls(cls, value: Union[str, List[str]]) -> Union[str, List[str]]:
        # 确保值不为空
        if not value:
            raise ValueError("URLs cannot be empty")

        urls_list = [value] if isinstance(value, str) else value

        if len(urls_list) > 1000:
            raise ValueError("URL count exceeds 1000")
        
        invalid = next((u for u in urls_list if not cls.validate_url(u)), None)
        if invalid is not None:
            raise ValueError(f"Invalid URL: {invalid}")        
                
        return value

    @staticmethod
    def validate_url(url: str) -> bool:
        if not url or len(url) > 2048:
            return False        
        try:
            parsed = urlparse(url)
            return (
                    parsed.scheme in {'http', 'https'}
                    and bool(parsed.netloc)
                    and not parsed.username
                    and not parsed.password
            )
        except Exception:
            return False           
        
                                                        
class RouterArgs(BaseModel):
    host: str
    port: int    
    worker_urls: Union[str, List[str]]
    policy: Literal["random", "round_robin", "cache_aware"] = "cache_aware"
    worker_startup_timeout_secs: int = Field(300, ge=3, le=500)
    worker_startup_check_interval: int = Field(3, ge=1, le=10)
    cache_threshold: float = Field(0.5, gt=0, lt=1)
    balance_abs_threshold: int = Field(32, ge=1, le=100)
    balance_rel_threshold: float = Field(1.0001, gt=1, lt=3)
    eviction_interval_secs: int = Field(60, ge=1, le=100)
    max_tree_size: int = Field(2 ** 24, ge=2 ** 15, le=2 ** 26)
    log_dir: str = Field(""),
    verbose: bool = Field(False)
    
    @field_validator('host')
    @classmethod
    def validate_host(cls, v: str) -> str:
        ipv4_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        
        if not (re.match(ipv4_pattern, v)):
            raise ValueError(f"host '{v}' is not a valid IP address")
        if re.match(ipv4_pattern, v):
            if not all(0 <= int(part) <= 255 for part in v.split('.')):
                raise ValueError(f"host '{v}' contains invalid IPv4 octets")
        return v
                
    @field_validator('port')
    @classmethod    
    def validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"port {v} is not a valid port number (1-65535)")
        return v
        
    @field_validator('worker_urls')
    @classmethod
    def validate_worker_urls(cls, v: Union[str, List[str]]) -> List[str]:
        urls = [v] if isinstance(v, str) else v
        for url in urls:
            if not isinstance(url, str):
                raise ValueError("Each URL must be a string")
            if not url.startswith(('http://', 'https://')):
                raise ValueError(f"URL must start with http:// or https://, got '{url}'")
            try:
                Url(url)
            except Exception:
                raise ValueError(f"Invalid URL format: {url}")
        return urls  # 统一转为 List[str]
        
    @field_validator('log_dir')
    @classmethod
    def validate_log_dir(cls, v: str) -> str:
        if v:
            path = Path(v).resolve()
            
            if not path.is_absolute():
                raise ValueError(f"Log path must be absolute, got: {v}")
                            
            parent = path.parent
            if not parent.exists():
                raise ValueError(f"Parent directory does not exist: {parent}")
                                                                                            
            if not os.access(parent, os.W_OK):
                raise ValueError(f"Parent directory is not writable: {parent}")
                            
            if path.exists() and not os.access(path, os.W_OK):
                raise ValueError(f"Log file exists but is not writable: {path}")
                
            return str(path)
        else:
            return ""
                
    @model_validator(mode='after')
    def check_host_port_not_in_worker_urls(self) -> "RouterArgs":
        host = self.host
        port = self.port
        worker_urls = self.worker_urls
            
        # 构造 host:port 字符串用于匹配
        host_port_combo = f"{host}:{port}"
                        
        # 检查每个 work_url 是否包含 host:port
        for url in worker_urls:
            if host_port_combo in url:
                raise ValueError(
                    f" '{host_port_combo}' is not allowed to appear in work_urls. "
                    f"Found in URL: {url}"
                )
                                    
        worker_startup_timeout_secs = self.worker_startup_timeout_secs
        worker_startup_check_interval = self.worker_startup_check_interval
        if worker_startup_check_interval > worker_startup_timeout_secs:
            raise ValueError(
                f" '{worker_startup_check_interval}' be less than {worker_startup_timeout_secs}. "
            )
                            
        return self


    def get_logits_processors(processors: Optional[LogitsProcessors], pattern: Optional[str]) -> Optional[list[Any]]:
        if processors and pattern:
            logits_processors = []
            for processor in processors:
                qualname = processor if isinstance(processor, str) else processor.qualname
                if not re.match(pattern, qualname):
                    raise ValueError(
                        f"Logits processor '{qualname}' is not allowed by this "
                        "server. See --logits-processor-pattern engine argument "
                        "for more information.")
                try:
                    logits_processor = resolve_obj_by_qualname(qualname)
                except Exception as e:
                    raise ValueError(
                        f"Logits processor '{qualname}' could not be resolved: {e}"
                    ) from e
                if isinstance(processor, LogitsProcessorConstructor):
                    logits_processor = logits_processor(*processor.args or [], **processor.kwargs or {})
                logits_processors.append(logits_processor)
            return logits_processors
        elif processors:
            raise ValueError(
                "The `logits_processors` argument is not supported by this "
                "server. See --logits-processor-pattern engine argugment "
                "for more information.")
        return None
                    
                    
    def resolve_obj_by_qualname(qualname: str) -> Any:
        """
        Resolve an object by its fully qualified name.
        """
        module_name, obj_name = qualname.rsplit(".", 1)
        module = importlib.import_module(module_name)
        return getattr(module, obj_name)
                                                        