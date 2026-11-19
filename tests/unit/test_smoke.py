def test_package_imports() -> None:
    import tokenvault.adapter
    import tokenvault.common
    import tokenvault.manager

    assert tokenvault.common and tokenvault.adapter and tokenvault.manager
