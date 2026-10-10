# test_addon.py and gui_capture.py run inside Blender (tests/run_tests.sh), not under pytest.
collect_ignore = ["test_addon.py", "gui_capture.py", "fixtures"]
