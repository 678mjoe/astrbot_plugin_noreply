from astrbot_plugin_noreply import main
from test_prompt_prefix import records


def test_prefix_description_is_content_free_and_stable_across_window_slides():
    cls = getattr(main, "PromptPrefix", None)
    assert callable(cls)
    planner = cls(max_rooms=1)
    describe = getattr(planner, "describe", None)
    assert callable(describe), "Content-free prefix diagnostics are missing"
    planner.serialize("room", records(1, 301), [])
    a = describe("room")
    planner.serialize("room", records(2, 302), [])
    b = describe("room")
    assert a == b and a["records"] == 200 and a["chars"] > 4096
    assert "完整" not in str(a) and len(a["fingerprint"]) == 12
