# CI 红验证探针（一次性分支，验证完即关 PR 删分支，绝不合并进 main）
# 目的：证明 pull_request 触发的 CI 能把失败如实标红。


def test_ci_probe_must_fail():
    assert False, "CI 探针：本测试故意失败，用于验证 CI 变红（方案 A：PR 红验证）"
