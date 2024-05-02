import os

def __Config_UI_Path_OLD():
    config_ui_path = None
    print(os.getcwd())
    if "/webai2api" in os.getcwd():
        config_ui_path = "UI/build"
        # config_ui_path = "webai2api/UI/build"
    else:
        # config_ui_path = "webai2api/webai2api/UI/build"
        config_ui_path = "webai2api/UI/build"
    
    return config_ui_path