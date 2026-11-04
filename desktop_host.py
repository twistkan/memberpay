"""Native window for the reusable edition, separate from the original app."""
import os
from pathlib import Path
import threading
import webview
from werkzeug.serving import make_server
import app as memberpay


def configure_data_paths():
    root = Path(os.environ.get('MEMBERPAY_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'MemberPayGroups' / 'workspace')))
    root.mkdir(parents=True, exist_ok=True)
    memberpay.APP_DIR = root
    memberpay.DB_PATH = root / 'memberpay.db'
    memberpay.PASSWORD_PATH = root / 'password.txt'  # Only used to recognize excluded legacy files.
    memberpay.BACKUP_DIR = root / 'backups'


def main():
    configure_data_paths()
    memberpay.init_db()
    server = make_server('127.0.0.1', 0, memberpay.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    window = webview.create_window('MemberPay · Groups', f'http://127.0.0.1:{server.server_port}', width=1240, height=820, min_size=(980,650), text_select=True)
    def choose_folder():
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None
    def save_file(name):
        result = window.create_file_dialog(webview.SAVE_DIALOG, save_filename=name)
        return result[0] if isinstance(result, (tuple,list)) else result
    memberpay.app.config.update(CHOOSE_FOLDER=choose_folder,SAVE_RECEIPT=save_file,SAVE_BACKUP=save_file)
    def close():
        try:
            memberpay.settings_tools.automatic_backup()
        finally:
            server.shutdown()
    window.events.closed += close
    webview.start(debug=False)


if __name__ == '__main__':
    main()
