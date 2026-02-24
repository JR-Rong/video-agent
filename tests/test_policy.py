from shortvideo_agent.safety.policy import check_text_policy, check_category


def test_category_allowlist():
    assert check_category("emotion").ok
    assert not check_category("politics").ok


def test_banned_keywords():
    assert not check_text_policy("今天刚刚发生的新闻").ok
    assert not check_text_policy("时政热点分析").ok
    assert check_text_policy("一个发生在小镇的虚构爱情故事").ok