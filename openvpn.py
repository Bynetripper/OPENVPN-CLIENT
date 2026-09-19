#!/usr/bin/env python3
"""
OpenVPN Manager - Dark Mode GUI (Thread-Safe Version)
- NO sudo needed to launch
- All UI updates happen on main thread via Qt signals
- Proper async command execution with QProcess
"""

import sys
import os
import shutil
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog,
    QComboBox, QSpinBox, QGroupBox, QFormLayout, QMessageBox, QProgressBar
)
from PyQt5.QtCore import Qt, QProcess, QObject, pyqtSignal, QThread
from PyQt5.QtGui import QFont

# ---------------- DARK THEME ----------------
DARK_QSS = """
QWidget {
    background-color: #1e1e1e;
    color: #e0e0e0;
    font-family: "Segoe UI", "Ubuntu", sans-serif;
    font-size: 10pt;
}
QMainWindow { background-color: #121212; }
QTabWidget::pane { border: 1px solid #333; background: #1e1e1e; }
QTabBar::tab {
    background: #2a2a2a; color: #bbb; padding: 8px 18px;
    border: 1px solid #333; border-bottom: none; margin-right: 2px;
}
QTabBar::tab:selected { background: #0078d4; color: white; }
QGroupBox {
    border: 1px solid #3a3a3a; border-radius: 6px;
    margin-top: 12px; padding-top: 16px; font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #4fc3f7; }
QLineEdit, QSpinBox, QComboBox {
    background: #2a2a2a; border: 1px solid #444; border-radius: 4px;
    padding: 6px; color: #e0e0e0;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #0078d4; }
QPushButton {
    background: #0078d4; color: white; border: none; border-radius: 4px;
    padding: 8px 16px; font-weight: bold;
}
QPushButton:hover { background: #1a8ae8; }
QPushButton:pressed { background: #005a9e; }
QPushButton:disabled { background: #444; color: #888; }
QPushButton#danger { background: #c62828; }
QPushButton#danger:hover { background: #e53935; }
QPushButton#success { background: #2e7d32; }
QPushButton#success:hover { background: #43a047; }
QTextEdit {
    background: #0f0f0f; color: #b0e0b0; border: 1px solid #333;
    border-radius: 4px; padding: 6px; font-family: "Consolas", monospace;
}
QProgressBar {
    border: 1px solid #444; border-radius: 4px; text-align: center;
    background: #2a2a2a; color: white;
}
QProgressBar::chunk { background: #0078d4; }
QLabel#title { font-size: 14pt; font-weight: bold; color: #4fc3f7; }
QLabel#subtitle { color: #999; font-size: 9pt; }
"""

# ---------------- PATHS ----------------
EASYRSA_DIR = Path.home() / "openvpn-manager" / "easyrsa"
OVPN_DIR = Path.home() / "openvpn-manager" / "configs"
SERVER_CONF = Path("/etc/openvpn/server.conf")


# ---------------- THREAD-SAFE WORKER ----------------
class CommandWorker(QObject):
    """Executes commands in a background thread and emits signals."""
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)  # return code

    def __init__(self, cmd, as_root=False, cwd=None):
        super().__init__()
        self.cmd = cmd
        self.as_root = as_root
        self.cwd = cwd
        self.process = None

    def run(self):
        """Execute the command."""
        try:
            full_cmd = self.cmd
            if self.as_root:
                if shutil.which("pkexec"):
                    full_cmd = ["pkexec"] + self.cmd
                elif shutil.which("sudo"):
                    full_cmd = ["sudo"] + self.cmd
                else:
                    self.log_signal.emit("[error] No pkexec or sudo found\n")
                    self.finished_signal.emit(-1)
                    return

            self.process = QProcess()
            if self.cwd:
                self.process.setWorkingDirectory(str(self.cwd))
            
            self.process.setProcessChannelMode(QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self._on_output)
            self.process.finished.connect(self._on_finished)
            
            self.log_signal.emit(f"[exec] {' '.join(full_cmd)}\n")
            self.process.start(full_cmd[0], full_cmd[1:])
            
        except Exception as e:
            self.log_signal.emit(f"[error] {str(e)}\n")
            self.finished_signal.emit(-1)

    def _on_output(self):
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self.log_signal.emit(data)

    def _on_finished(self, exit_code, exit_status):
        self.finished_signal.emit(exit_code)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenVPN Manager")
        self.resize(900, 680)
        self.vpn_process = None
        self.workers = []  # Keep references to prevent garbage collection

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        title = QLabel("🔒 OpenVPN Manager")
        title.setObjectName("title")
        root.addWidget(title)
        sub = QLabel("Install • Configure Server • Connect as Client")
        sub.setObjectName("subtitle")
        root.addWidget(sub)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self.tabs.addTab(self.build_install_tab(), "📦 Install")
        self.tabs.addTab(self.build_server_tab(), "🖥️ Server Setup")
        self.tabs.addTab(self.build_client_tab(), "🔌 Client Connect")

        # Log panel at bottom
        log_group = QGroupBox("Activity Log")
        log_lay = QVBoxLayout(log_group)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)
        log_lay.addWidget(self.log)
        root.addWidget(log_group)

    def log_msg(self, msg):
        """Thread-safe logging via Qt signal (called from main thread)."""
        self.log.moveCursor(self.log.textCursor().End)
        self.log.insertPlainText(msg)
        self.log.ensureCursorVisible()

    def run_command(self, cmd, as_root=False, cwd=None, callback=None):
        """Run a command asynchronously with thread-safe logging."""
        worker = CommandWorker(cmd, as_root, cwd)
        thread = QThread()
        worker.moveToThread(thread)
        
        # Connect signals
        worker.log_signal.connect(self.log_msg)
        worker.finished_signal.connect(lambda rc: self._on_cmd_finished(rc, callback, worker, thread))
        
        thread.started.connect(worker.run)
        
        # Keep references
        self.workers.append((worker, thread))
        
        thread.start()

    def _on_cmd_finished(self, rc, callback, worker, thread):
        """Called when a command finishes."""
        thread.quit()
        thread.wait()
        if callback:
            callback(rc)
        # Clean up
        self.workers.remove((worker, thread))

    # ---------- INSTALL TAB ----------
    def build_install_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        info = QLabel(
            "Install OpenVPN and Easy-RSA (required for both server and client modes).\n"
            "Uses your system package manager (apt). You will be prompted for your password."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        self.install_progress = QProgressBar()
        self.install_progress.setRange(0, 0)
        self.install_progress.hide()
        lay.addWidget(self.install_progress)

        btn_row = QHBoxLayout()
        self.btn_install = QPushButton("Install OpenVPN + Easy-RSA")
        self.btn_install.clicked.connect(self.do_install)
        btn_row.addWidget(self.btn_install)

        self.btn_check = QPushButton("Check Installation")
        self.btn_check.clicked.connect(self.check_installed)
        btn_row.addWidget(self.btn_check)
        lay.addLayout(btn_row)

        self.install_status = QLabel("")
        lay.addWidget(self.install_status)
        lay.addStretch()
        return w

    def check_installed(self):
        ovpn = shutil.which("openvpn") is not None
        ersa = (Path("/usr/share/easy-rsa/easyrsa").exists() or
                Path("/usr/libexec/easy-rsa/easyrsa").exists() or
                shutil.which("easyrsa") is not None)
        msg = []
        msg.append(f"OpenVPN binary: {'✅ found' if ovpn else '❌ missing'}")
        msg.append(f"Easy-RSA:       {'✅ found' if ersa else '❌ missing'}")
        self.install_status.setText("\n".join(msg))
        self.log_msg("[check] " + " | ".join(msg) + "\n")

    def do_install(self):
        self.btn_install.setEnabled(False)
        self.install_progress.show()
        self.log_msg("[install] Starting package installation...\n")

        def on_update_done(rc):
            if rc != 0:
                self.log_msg("[install] apt update failed, continuing anyway...\n")
            self.run_command(
                ["apt-get", "install", "-y", "openvpn", "easy-rsa"],
                as_root=True,
                callback=on_install_done
            )

        def on_install_done(rc):
            if rc == 0:
                self.log_msg("[install] Packages installed successfully\n")
                # Enable IP forwarding
                self.run_command(
                    ["sed", "-i", "s/^#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/",
                     "/etc/sysctl.conf"],
                    as_root=True,
                    callback=on_sysctl_done
                )
            else:
                self.log_msg(f"[install] Installation failed (rc={rc})\n")
                self.install_progress.hide()
                self.btn_install.setEnabled(True)

        def on_sysctl_done(rc):
            self.run_command(["sysctl", "-p"], as_root=True, callback=on_sysctl_p_done)

        def on_sysctl_p_done(rc):
            self.log_msg("[install] IP forwarding enabled\n")
            self.check_installed()
            self.install_progress.hide()
            self.btn_install.setEnabled(True)

        self.run_command(["apt-get", "update"], as_root=True, callback=on_update_done)

    # ---------- SERVER TAB ----------
    def build_server_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        # Server settings
        grp = QGroupBox("Server Configuration")
        form = QFormLayout(grp)
        self.srv_ip = QLineEdit("10.8.0.0")
        self.srv_mask = QLineEdit("255.255.255.0")
        self.srv_port = QSpinBox()
        self.srv_port.setRange(1, 65535)
        self.srv_port.setValue(1194)
        self.srv_proto = QComboBox()
        self.srv_proto.addItems(["udp", "tcp"])
        self.srv_dns = QLineEdit("8.8.8.8")
        self.srv_ext_ip = QLineEdit()
        form.addRow("VPN Subnet:", self.srv_ip)
        form.addRow("Subnet Mask:", self.srv_mask)
        form.addRow("Port:", self.srv_port)
        form.addRow("Protocol:", self.srv_proto)
        form.addRow("DNS for clients:", self.srv_dns)
        form.addRow("Server Public IP/Domain:", self.srv_ext_ip)
        lay.addWidget(grp)

        # Actions
        actions = QHBoxLayout()
        b1 = QPushButton("1. Init PKI (Easy-RSA)")
        b1.clicked.connect(self.init_pki)
        b2 = QPushButton("2. Build CA + Server")
        b2.clicked.connect(self.build_server)
        b3 = QPushButton("3. Create Client Credentials")
        b3.clicked.connect(self.create_client)
        b4 = QPushButton("4. Install Server Config")
        b4.clicked.connect(self.install_server_conf)
        for b in (b1, b2, b3, b4):
            actions.addWidget(b)
        lay.addLayout(actions)

        # Client name
        client_row = QHBoxLayout()
        client_row.addWidget(QLabel("Client name:"))
        self.client_name = QLineEdit("myclient")
        client_row.addWidget(self.client_name)
        self.client_with_pass = QComboBox()
        self.client_with_pass.addItems(["Certificate-only auth", "Username+Password (PAM)"])
        client_row.addWidget(self.client_with_pass)
        lay.addLayout(client_row)

        lay.addStretch()
        return w

    def init_pki(self):
        self.log_msg("[pki] Initializing Easy-RSA PKI...\n")
        EASYRSA_DIR.mkdir(parents=True, exist_ok=True)
        OVPN_DIR.mkdir(parents=True, exist_ok=True)

        # Find easy-rsa source
        easyrsa_src = None
        for src in ("/usr/share/easy-rsa", "/usr/libexec/easy-rsa"):
            if Path(src).exists():
                easyrsa_src = src
                break

        if not easyrsa_src:
            self.log_msg("[pki] ❌ Easy-RSA not found. Install it first.\n")
            return

        def on_copy_done(rc):
            if rc == 0:
                self.run_command(
                    ["./easyrsa", "init-pki"],
                    cwd=EASYRSA_DIR,
                    callback=on_init_done
                )
            else:
                self.log_msg("[pki] ❌ Failed to copy easy-rsa\n")

        def on_init_done(rc):
            if rc == 0:
                self.log_msg("[pki] ✅ PKI initialized\n")
            else:
                self.log_msg(f"[pki] ❌ init-pki failed (rc={rc})\n")

        self.run_command(
            ["cp", "-r", f"{easyrsa_src}/.", str(EASYRSA_DIR)],
            callback=on_copy_done
        )

    def build_server(self):
        self.log_msg("[server] Building CA and server certificates...\n")

        def on_ca_done(rc):
            if rc == 0:
                self.log_msg("[ca] ✅ CA created\n")
                self.run_command(
                    ["./easyrsa", "build-server-full", "server", "nopass"],
                    cwd=EASYRSA_DIR,
                    callback=on_server_done
                )
            else:
                self.log_msg(f"[ca] ❌ build-ca failed (rc={rc})\n")

        def on_server_done(rc):
            if rc == 0:
                self.log_msg("[server] ✅ Server cert created\n")
                self.run_command(
                    ["./easyrsa", "gen-dh"],
                    cwd=EASYRSA_DIR,
                    callback=on_dh_done
                )
            else:
                self.log_msg(f"[server] ❌ build-server-full failed (rc={rc})\n")

        def on_dh_done(rc):
            if rc == 0:
                self.log_msg("[dh] ✅ DH params generated\n")
                self.run_command(
                    ["openvpn", "--genkey", "--secret", "ta.key"],
                    cwd=EASYRSA_DIR,
                    callback=on_ta_done
                )
            else:
                self.log_msg(f"[dh] ❌ gen-dh failed (rc={rc})\n")

        def on_ta_done(rc):
            if rc == 0:
                self.log_msg("[tls-auth] ✅ ta.key generated\n")
                self.log_msg("[server] ✅ CA + server certs ready\n")
            else:
                self.log_msg(f"[tls-auth] ❌ ta.key generation failed (rc={rc})\n")

        # Set environment for non-interactive CA build
        os.environ["EASYRSA_BATCH"] = "1"
        os.environ["EASYRSA_REQ_CN"] = "OpenVPN-CA"

        self.run_command(
            ["./easyrsa", "build-ca", "nopass"],
            cwd=EASYRSA_DIR,
            callback=on_ca_done
        )

    def create_client(self):
        name = self.client_name.text().strip() or "myclient"
        use_pass = self.client_with_pass.currentIndex() == 1

        self.log_msg(f"[client] Creating client '{name}'...\n")

        def on_client_done(rc):
            if rc == 0:
                self.log_msg(f"[client] ✅ Client cert created\n")
                self._build_ovpn_file(name, use_pass)
            else:
                self.log_msg(f"[client] ❌ build-client-full failed (rc={rc})\n")

        if use_pass:
            cmd = ["./easyrsa", "build-client-full", name]
        else:
            cmd = ["./easyrsa", "build-client-full", name, "nopass"]

        self.run_command(cmd, cwd=EASYRSA_DIR, callback=on_client_done)

    def _build_ovpn_file(self, name, use_pass):
        """Build the .ovpn file with inline certificates."""
        try:
            pki = EASYRSA_DIR / "pki"
            ca = (pki / "ca.crt").read_text()
            cert = (pki / "issued" / f"{name}.crt").read_text()
            key = (pki / "private" / f"{name}.key").read_text()
            ta = (EASYRSA_DIR / "ta.key").read_text()

            ext_ip = self.srv_ext_ip.text().strip() or "YOUR_SERVER_IP"
            port = self.srv_port.value()
            proto = self.srv_proto.currentText()

            ovpn = f"""client
dev tun
proto {proto}
remote {ext_ip} {port}
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
auth SHA256
cipher AES-256-GCM
verb 3
<ca>
{ca}</ca>
<cert>
{cert}</cert>
<key>
{key}</key>
<tls-auth>
{ta}</tls-auth>
key-direction 1
"""
            if use_pass:
                ovpn += "auth-user-pass\n"

            OVPN_DIR.mkdir(parents=True, exist_ok=True)
            out_file = OVPN_DIR / f"{name}.ovpn"
            out_file.write_text(ovpn)
            os.chmod(out_file, 0o600)
            self.log_msg(f"[client] ✅ Saved: {out_file}\n")

            if use_pass:
                self.log_msg(f"[pam] Create system user with: sudo useradd -m {name} && sudo passwd {name}\n")

        except Exception as e:
            self.log_msg(f"[client] ❌ Error building .ovpn: {e}\n")

    def install_server_conf(self):
        self.log_msg("[server] Writing /etc/openvpn/server.conf...\n")
        ip = self.srv_ip.text()
        mask = self.srv_mask.text()
        port = self.srv_port.value()
        proto = self.srv_proto.currentText()
        dns = self.srv_dns.text()
        use_pass = self.client_with_pass.currentIndex() == 1

        pki = EASYRSA_DIR / "pki"
        conf = f"""port {port}
proto {proto}
dev tun
ca {pki}/ca.crt
cert {pki}/issued/server.crt
key {pki}/private/server.key
dh {pki}/dh.pem
tls-auth {EASYRSA_DIR}/ta.key 0
server {ip} {mask}
ifconfig-pool-persist ipp.txt
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS {dns}"
keepalive 10 120
cipher AES-256-GCM
auth SHA256
persist-key
persist-tun
status openvpn-status.log
verb 3
explicit-exit-notify 0
"""
        if use_pass:
            conf += "plugin /usr/lib/openvpn/openvpn-plugin-auth-pam.so login\n"

        # Write to temp file first
        tmp = "/tmp/server.conf.tmp"
        Path(tmp).write_text(conf)

        def on_cp_done(rc):
            if rc == 0:
                self.run_command(
                    ["chmod", "644", str(SERVER_CONF)],
                    as_root=True,
                    callback=on_chmod_done
                )
            else:
                self.log_msg(f"[server] ❌ Failed to copy config (rc={rc})\n")

        def on_chmod_done(rc):
            self.run_command(
                ["systemctl", "enable", "openvpn@server"],
                as_root=True,
                callback=on_enable_done
            )

        def on_enable_done(rc):
            self.run_command(
                ["systemctl", "restart", "openvpn@server"],
                as_root=True,
                callback=on_restart_done
            )

        def on_restart_done(rc):
            if rc == 0:
                self.log_msg("[server] ✅ Service started\n")
                # Add iptables rule
                self.run_command(
                    ["iptables", "-t", "nat", "-A", "POSTROUTING",
                     "-s", f"{ip}/24", "-o", "eth0", "-j", "MASQUERADE"],
                    as_root=True,
                    callback=on_iptables_done
                )
            else:
                self.log_msg(f"[server] ❌ Service restart failed (rc={rc})\n")

        def on_iptables_done(rc):
            if rc == 0:
                self.log_msg("[server] ✅ NAT rule added\n")
                self.log_msg("[server] ✅ Server installed and started\n")
            else:
                self.log_msg(f"[server] ⚠️ iptables rule failed (rc={rc}), but server is running\n")

        self.run_command(
            ["cp", tmp, str(SERVER_CONF)],
            as_root=True,
            callback=on_cp_done
        )

    # ---------- CLIENT TAB ----------
    def build_client_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        info = QLabel(
            "Import an existing .ovpn file (received from another OpenVPN server) "
            "and connect this machine as a client."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        row = QHBoxLayout()
        self.ovpn_path = QLineEdit()
        self.ovpn_path.setPlaceholderText("Path to .ovpn config file...")
        row.addWidget(self.ovpn_path)
        b_browse = QPushButton("Browse")
        b_browse.clicked.connect(self.browse_ovpn)
        row.addWidget(b_browse)
        lay.addLayout(row)

        # Optional credentials
        cred_grp = QGroupBox("Credentials (only if server requires username/password)")
        cred_lay = QFormLayout(cred_grp)
        self.c_user = QLineEdit()
        self.c_pass = QLineEdit()
        self.c_pass.setEchoMode(QLineEdit.Password)
        cred_lay.addRow("Username:", self.c_user)
        cred_lay.addRow("Password:", self.c_pass)
        lay.addWidget(cred_grp)

        btn_row = QHBoxLayout()
        self.btn_connect = QPushButton("▶ Connect")
        self.btn_connect.setObjectName("success")
        self.btn_connect.clicked.connect(self.connect_vpn)
        btn_row.addWidget(self.btn_connect)

        self.btn_disconnect = QPushButton("■ Disconnect")
        self.btn_disconnect.setObjectName("danger")
        self.btn_disconnect.clicked.connect(self.disconnect_vpn)
        btn_row.addWidget(self.btn_disconnect)
        lay.addLayout(btn_row)

        self.conn_status = QLabel("Status: Idle")
        lay.addWidget(self.conn_status)
        lay.addStretch()
        return w

    def browse_ovpn(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select .ovpn file", str(Path.home()),
            "OpenVPN Config (*.ovpn *.conf);;All Files (*)"
        )
        if path:
            self.ovpn_path.setText(path)

    def connect_vpn(self):
        path = self.ovpn_path.text().strip()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "Missing config", "Please select a valid .ovpn file.")
            return
        if not shutil.which("openvpn"):
            QMessageBox.warning(self, "OpenVPN missing", "Install OpenVPN first (Install tab).")
            return

        # Write credentials file if provided
        cred_file = None
        if self.c_user.text():
            cred_file = "/tmp/ovpn_creds.tmp"
            Path(cred_file).write_text(
                f"{self.c_user.text()}\n{self.c_pass.text()}\n"
            )
            os.chmod(cred_file, 0o600)

        cmd = ["openvpn", "--config", path]
        if cred_file:
            cmd += ["--auth-user-pass", cred_file]

        self.vpn_process = QProcess(self)
        self.vpn_process.setProcessChannelMode(QProcess.MergedChannels)
        self.vpn_process.readyRead.connect(self._read_vpn_output)
        self.vpn_process.finished.connect(self._vpn_finished)
        
        # Use pkexec for privilege elevation
        if shutil.which("pkexec"):
            self.vpn_process.start("pkexec", cmd)
        else:
            self.vpn_process.start("sudo", cmd)
        
        self.conn_status.setText("Status: Connecting...")
        self.log_msg(f"[vpn] Starting: {' '.join(cmd)}\n")

    def _read_vpn_output(self):
        data = bytes(self.vpn_process.readAll()).decode(errors="replace")
        self.log_msg(data)
        if "Initialization Sequence Completed" in data:
            self.conn_status.setText("Status: ✅ Connected")
        elif "AUTH_FAILED" in data:
            self.conn_status.setText("Status: ❌ Authentication failed")

    def _vpn_finished(self, code, status):
        self.conn_status.setText(f"Status: Disconnected (code {code})")
        self.log_msg(f"[vpn] Process exited code={code}\n")

    def disconnect_vpn(self):
        if self.vpn_process and self.vpn_process.state() != QProcess.NotRunning:
            self.vpn_process.terminate()
            self.conn_status.setText("Status: Disconnected")
            self.log_msg("[vpn] Disconnected\n")


# ---------------- ENTRY POINT ----------------
def main():
    # Check if running as root
    if os.geteuid() == 0:
        print("❌ ERROR: Do not run this app with sudo!")
        print("   The app will request elevation only when needed.")
        print("   Run it as: python3 openvpn.py")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()