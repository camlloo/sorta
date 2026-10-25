"""一次性补丁：llm_factory 改为按运行时配置构造。执行后删除。"""

from pathlib import Path

# 1) main.py：默认工厂按运行时配置构造
p = Path("sorta/api/main.py")
src = p.read_text(encoding="utf-8")
old = '''def _default_llm_factory() -> Any:
    from sorta.agent.llm import OpenAICompatClient
    from sorta.errors import ModelUnavailableError

    api_key = os.environ.get("SORTA_API_KEY")
    if not api_key:
        raise ModelUnavailableError("缺少 SORTA_API_KEY 环境变量，无法调用模型")
    return OpenAICompatClient(
        api_key=api_key, base_url=os.environ.get("SORTA_BASE_URL") or None
    )'''
new = '''def _default_llm_factory(model: str, api_key: str, base_url: str | None) -> Any:
    from sorta.agent.llm import OpenAICompatClient

    return OpenAICompatClient(api_key=api_key, base_url=base_url)'''
assert old in src, "main.py edit"
src = src.replace(old, new, 1)
p.write_text(src, encoding="utf-8", newline="\n")

# 2) deps.make_llm：走 app.state.llm_factory（测试可注入 Mock）
p = Path("sorta/api/deps.py")
src = p.read_text(encoding="utf-8")
old2 = '''    from sorta.agent.llm import OpenAICompatClient

    client = OpenAICompatClient(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)
    return cfg["model"], client'''
new2 = '''    client = request.app.state.llm_factory(
        cfg["model"], cfg["api_key"], cfg["base_url"] or None
    )
    return cfg["model"], client'''
assert old2 in src, "deps.py edit"
src = src.replace(old2, new2, 1)
p.write_text(src, encoding="utf-8", newline="\n")

# 3) 测试 fixture：提供 api_key 环境回退 + 工厂签名适配
p = Path("tests/test_api.py")
src = p.read_text(encoding="utf-8")
old3 = '''    monkeypatch.delenv("SORTA_API_KEY", raising=False)
    monkeypatch.setenv("SORTA_MODEL", "mock-model")'''
new3 = '''    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")'''
assert old3 in src, "test fixture env edit"
src = src.replace(old3, new3, 1)
old4 = "    app = create_app(config=config, llm_factory=_make_mock_llm, enable_auth=True)"
new4 = (
    "    app = create_app(\n"
    "        config=config,\n"
    "        llm_factory=lambda model, api_key, base_url: _make_mock_llm(),\n"
    "        enable_auth=True,\n"
    "    )"
)
assert old4 in src, "test fixture factory edit"
src = src.replace(old4, new4, 1)
p.write_text(src, encoding="utf-8", newline="\n")
print("all patches applied")
