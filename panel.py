"""FraguMC-bot — aplikacja do konfiguracji bota Discord.

Uruchomienie:  python panel.py
Wymaga tylko Pythona (tkinter jest wbudowany).
Na hostingu ustaw zmienne: API_KEY (tajny klucz) i wystaw port API_PORT.
"""
import json
import tkinter as tk
from tkinter import messagebox, ttk
import urllib.request
import urllib.error


class Api:
    def __init__(self):
        self.host = ""
        self.port = 8080
        self.key = ""

    def _req(self, method, path, body=None):
        url = f"http://{self.host}:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"X-API-Key": self.key,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def overview(self):
        return self._req("GET", "/overview")

    def channels(self, guild_id):
        return self._req("GET", f"/channels?guild_id={guild_id}")

    def set_server(self, address, name):
        return self._req("POST", "/server", {"address": address, "name": name})

    def remove_server(self):
        return self._req("DELETE", "/server")

    def mc_status(self):
        return self._req("GET", "/mcstatus")

    def set_kary(self, guild_id, channel_id):
        return self._req("POST", "/kary", {"guild_id": guild_id, "channel_id": channel_id})

    def prop(self, guild_id, channel_id, action):
        return self._req("POST", "/propozycje",
                         {"guild_id": guild_id, "channel_id": channel_id, "action": action})


class Panel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FraguMC-bot — panel")
        self.geometry("480x560")
        self.api = Api()
        self.guilds = []
        self.channels = []
        self._build_connect()
        self._build_tabs()

    def _build_connect(self):
        f = ttk.LabelFrame(self, text="Połączenie", padding=8)
        f.pack(fill="x", padx=10, pady=10)
        ttk.Label(f, text="Host:").grid(row=0, column=0, sticky="w")
        self.e_host = ttk.Entry(f, width=22)
        self.e_host.grid(row=0, column=1, padx=4)
        ttk.Label(f, text="Port:").grid(row=0, column=2, sticky="w")
        self.e_port = ttk.Entry(f, width=7)
        self.e_port.insert(0, "8080")
        self.e_port.grid(row=0, column=3, padx=4)
        ttk.Label(f, text="Klucz:").grid(row=1, column=0, sticky="w", pady=4)
        self.e_key = ttk.Entry(f, width=30, show="*")
        self.e_key.grid(row=1, column=1, columnspan=2, padx=4, sticky="we")
        ttk.Button(f, text="Połącz", command=self.connect).grid(row=1, column=3, padx=4)
        self.lbl_status = ttk.Label(f, text="Nie połączono", foreground="red")
        self.lbl_status.grid(row=2, column=0, columnspan=4, sticky="w")

    def _build_tabs(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=5)

        self.tab_server = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.tab_server, text="Serwer MC")
        ttk.Label(self.tab_server, text="Adres (IP/domena):").pack(anchor="w")
        self.e_addr = ttk.Entry(self.tab_server, width=40)
        self.e_addr.pack(fill="x", pady=2)
        ttk.Label(self.tab_server, text="Nazwa:").pack(anchor="w")
        self.e_name = ttk.Entry(self.tab_server, width=40)
        self.e_name.pack(fill="x", pady=2)
        row = ttk.Frame(self.tab_server)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="Zapisz", command=self.save_server).pack(side="left", padx=2)
        ttk.Button(row, text="Usuń", command=self.del_server).pack(side="left", padx=2)
        ttk.Button(row, text="Sprawdź status", command=self.check_status).pack(side="left", padx=2)
        self.txt_status = tk.Text(self.tab_server, height=8, width=50, state="disabled")
        self.txt_status.pack(fill="both", expand=True, pady=4)

        self.tab_kary = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.tab_kary, text="Kanał kar")
        ttk.Label(self.tab_kary, text="Serwer:").pack(anchor="w")
        self.cb_guild = ttk.Combobox(self.tab_kary, state="readonly", width=45)
        self.cb_guild.pack(fill="x", pady=2)
        self.cb_guild.bind("<<ComboboxSelected>>", lambda e: self.load_guild())
        ttk.Label(self.tab_kary, text="Kanał kar:").pack(anchor="w")
        self.cb_kary = ttk.Combobox(self.tab_kary, state="readonly", width=45)
        self.cb_kary.pack(fill="x", pady=2)
        ttk.Button(self.tab_kary, text="Zapisz kanał kar", command=self.save_kary).pack(pady=8)

        self.tab_prop = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.tab_prop, text="Propozycje")
        ttk.Label(self.tab_prop, text="Kanały propozycji:").pack(anchor="w")
        self.lb_prop = tk.Listbox(self.tab_prop, height=6)
        self.lb_prop.pack(fill="both", expand=True, pady=2)
        ttk.Label(self.tab_prop, text="Kanał:").pack(anchor="w")
        self.cb_prop = ttk.Combobox(self.tab_prop, state="readonly", width=45)
        self.cb_prop.pack(fill="x", pady=2)
        row2 = ttk.Frame(self.tab_prop)
        row2.pack(fill="x", pady=6)
        ttk.Button(row2, text="Dodaj", command=lambda: self.prop_action("add")).pack(side="left", padx=2)
        ttk.Button(row2, text="Usuń", command=lambda: self.prop_action("remove")).pack(side="left", padx=2)
        ttk.Button(row2, text="Odśwież", command=self.load_guild).pack(side="left", padx=2)

    def guild_id(self):
        i = self.cb_guild.current()
        return self.guilds[i]["id"] if 0 <= i < len(self.guilds) else None

    def channel_id_by_name(self, combo):
        name = combo.get()
        for c in self.channels:
            if c["name"] == name:
                return c["id"]
        return None

    def connect(self):
        self.api.host = self.e_host.get().strip()
        try:
            self.api.port = int(self.e_port.get().strip())
        except ValueError:
            messagebox.showerror("Błąd", "Zły port")
            return
        self.api.key = self.e_key.get().strip()
        data = self.api.overview()
        if not data.get("ok"):
            self.lbl_status.config(text=f"Błąd: {data.get('error')}", foreground="red")
            return
        self.lbl_status.config(text="Połączono ✓", foreground="green")
        self.guilds = data.get("guilds", [])
        self.cb_guild["values"] = [g["name"] for g in self.guilds]
        if self.guilds:
            self.cb_guild.current(0)
            self.load_guild()
        srv = data.get("mc_server") or {}
        self.e_addr.delete(0, "end")
        self.e_addr.insert(0, srv.get("address") or "")
        self.e_name.delete(0, "end")
        self.e_name.insert(0, srv.get("name") or "")

    def load_guild(self):
        gid = self.guild_id()
        if not gid:
            return
        data = self.api.channels(gid)
        if not data.get("ok"):
            messagebox.showerror("Błąd", str(data.get("error")))
            return
        self.channels = data["channels"]
        names = [c["name"] for c in self.channels]
        self.cb_kary["values"] = names
        self.cb_prop["values"] = names
        ov = self.api.overview()
        cfg = (ov.get("config") or {}).get(str(gid), {}) if ov.get("ok") else {}
        kid = str(cfg.get("kary_channel") or "")
        for i, c in enumerate(self.channels):
            if str(c["id"]) == kid:
                self.cb_kary.current(i)
                break
        self.lb_prop.delete(0, "end")
        prop_ids = {str(x) for x in cfg.get("propozycje", [])}
        for c in self.channels:
            if str(c["id"]) in prop_ids:
                self.lb_prop.insert("end", f"#{c['name']}")

    def save_server(self):
        data = self.api.set_server(self.e_addr.get().strip(), self.e_name.get().strip())
        if data.get("ok"):
            messagebox.showinfo("OK", f"Zapisano: {data['name']} ({data['address']}, {data['type']})")
        else:
            messagebox.showerror("Błąd", str(data.get("error")))

    def del_server(self):
        if messagebox.askyesno("Potwierdź", "Usunąć serwer z listy?"):
            data = self.api.remove_server()
            messagebox.showinfo("OK" if data.get("ok") else "Błąd", str(data.get("error", "Usunięto")))

    def check_status(self):
        data = self.api.mc_status()
        self.txt_status.config(state="normal")
        self.txt_status.delete("1.0", "end")
        if not data.get("ok"):
            self.txt_status.insert("end", f"Błąd: {data.get('error')}")
        else:
            s = data["status"]
            srv = data["server"]
            linie = [f"Serwer: {srv['name']} ({srv['address']})",
                     f"Online: {'TAK' if s.get('online') else 'NIE'}"]
            if s.get("online"):
                linie += [f"Gracze: {s['players_online']}/{s['players_max']}",
                          f"Wersja: {s['version']}",
                          f"Ping: {s.get('latency')} ms" if s.get("latency") is not None else "",
                          f"Opis: {(s.get('motd') or '').strip()}"]
            self.txt_status.insert("end", "\n".join(x for x in linie if x))
        self.txt_status.config(state="disabled")

    def save_kary(self):
        gid = self.guild_id()
        cid = self.channel_id_by_name(self.cb_kary)
        if not gid or not cid:
            messagebox.showerror("Błąd", "Wybierz serwer i kanał")
            return
        data = self.api.set_kary(gid, cid)
        if data.get("ok"):
            messagebox.showinfo("OK", "Zapisano kanał kar")
        else:
            messagebox.showerror("Błąd", str(data.get("error")))

    def prop_action(self, action):
        gid = self.guild_id()
        cid = self.channel_id_by_name(self.cb_prop)
        if not gid or not cid:
            messagebox.showerror("Błąd", "Wybierz serwer i kanał")
            return
        data = self.api.prop(gid, cid, action)
        if data.get("ok"):
            self.load_guild()
        else:
            messagebox.showerror("Błąd", str(data.get("error")))


if __name__ == "__main__":
    Panel().mainloop()
