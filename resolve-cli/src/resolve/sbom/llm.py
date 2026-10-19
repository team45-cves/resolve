"""Handles conversion from CVEs to Vulnerability JSON format"""
from __future__ import absolute_import

class LLMError(BaseException):
    pass
    
class AffectedNotFoundError(LLMError):
    pass

class APIConnectionError(LLMError):
    pass

class APIKeyError(LLMError):
    pass

class LLM:
    prompt: str = "The following text describes a CVE. If it specifies which file and function is vulnerable, end your reply with the exact format `{file name}:{function name}`. Otherwise, if this is not apparent, end your reply with `N/A`. Use thinking and tools if needed, but ensure the final line of your response is exactly the expected format."

    def _query(self, payload : str) -> str:
        raise NotImplementedError
        
    def get_affected(self, description: str) -> tuple[str, str]:
        payload = self.prompt + description
        try:
            response = self._query(payload).strip()
        except LLMError as e:
            raise e
        except BaseException as e:
            raise LLMError from e
        if response == 'N/A':
            raise AffectedNotFoundError
        sp = response.split(':')
        if len(sp) != 2:
            raise LLMError(f"Invalid LLM response: '{sp}'")
        return (sp[0], sp[1])
        
        
class Ollama(LLM):
    def __init__(self, model: str = 'gemma3', server: str = 'http://localhost:11434'):
        import ollama
        self.api = ollama.generate
        self.model = model            
        
    def _query(self, payload: str) -> str:
        resp = self.api(self.model, payload)
        return resp.response        

class Opencode(LLM):
    def __init__(self, model: str = "openai/gpt-5.3-codex-spark"):
        self.model = model

    def _query(self, payload: str) -> str:
        import subprocess
        import json
        import os

        env = os.environ.copy()
        env["OPENCODE_PERMISSION"] = json.dumps(
            {
                "webfetch": "allow",
                "websearch": "allow",
                "codesearch": "allow",
            }
        )
        agent_command = ["opencode", "run", "--agent", "build"]
        agent_command += ["--model", self.model]

        process = subprocess.run(
            agent_command + [payload],
            env=env,
            text=True,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        if process.returncode != 0:
            print(process.stderr)
            raise LLMError(process.stderr)

        return process.stdout.splitlines()[-1]


class Claude(LLM):
    """Shells out to the Claude Code CLI. Auth comes from ANTHROPIC_API_KEY or a prior `claude` login."""

    def __init__(self, model: str | None = None):
        import shutil

        if shutil.which("claude") is None:
            raise APIConnectionError() from FileNotFoundError("`claude` CLI not found on PATH")
        self.model = model

    def _query(self, payload: str) -> str:
        import subprocess

        # Web tools are pre-approved (as for Opencode) so the CLI never stops to
        # ask for permission; everything else stays denied.
        agent_command = ["claude", "-p", "--allowedTools", "WebFetch", "WebSearch"]
        if self.model is not None:
            agent_command += ["--model", self.model]

        # The prompt goes over stdin: --allowedTools is variadic and would
        # swallow a trailing positional prompt.
        process = subprocess.run(
            agent_command,
            input=payload,
            text=True,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        if process.returncode != 0:
            print(process.stderr)
            raise LLMError(process.stderr or process.stdout)

        lines = process.stdout.strip().splitlines()
        if not lines:
            raise LLMError("Empty response")
        return lines[-1].strip().strip("`")


class Gemini(LLM):
    def __init__(self, model: str = 'gemini-2.5-flash', **kwargs) -> None:
        from google import genai
        try:
            self.client = genai.Client(**kwargs)
        except ValueError as e:
            raise APIKeyError() from e
        self.model = model
        
    def _query(self, payload: str) -> str:
        resp = self.client.models.generate_content(model=self.model, contents=payload).text
        if not resp:
            raise LLMError("Empty response")
        return resp
