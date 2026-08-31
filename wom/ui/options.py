"""The Options dialog: edit the username list and the update settings."""

import tkinter as tk
from tkinter import ttk

from .. import theme
from ..config import normalise_usernames
from ..scheduler import describe_schedule
from ..summaries import SUMMARY_MODELS


class OptionsDialog(tk.Toplevel):
    """Modal settings window. `on_saved(changes)` runs after a successful save."""

    def __init__(self, master, config, on_saved=None):
        super().__init__(master)
        self.config_obj = config
        self.on_saved = on_saved
        self.result = None

        self.title("Options")
        self.transient(master)
        self.resizable(True, True)
        self.minsize(460, 480)

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        # -- usernames ----------------------------------------------------
        ttk.Label(outer, text="Tracked usernames", font=("", 10, "bold")).pack(anchor=tk.W)
        ttk.Label(
            outer,
            text="One RuneScape name per line. Names are updated in this order.",
            foreground=theme.MUTED,
        ).pack(anchor=tk.W, pady=(0, 6))

        text_holder = ttk.Frame(outer)
        text_holder.pack(fill=tk.BOTH, expand=True)
        self.names_text = tk.Text(text_holder, height=12, width=40, wrap=tk.NONE,
                                  undo=True, font=("Consolas", 10),
                                  background=theme.RAISED, foreground=theme.INK,
                                  insertbackground=theme.INK,
                                  relief=tk.FLAT, highlightthickness=1,
                                  highlightbackground=theme.LINE)
        scroll = ttk.Scrollbar(text_holder, orient=tk.VERTICAL, command=self.names_text.yview)
        self.names_text.configure(yscrollcommand=scroll.set)
        self.names_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.names_text.insert("1.0", "\n".join(config.get("usernames", [])))

        self.count_label = ttk.Label(outer, text="", foreground=theme.MUTED)
        self.count_label.pack(anchor=tk.W, pady=(4, 10))
        self.names_text.bind("<KeyRelease>", lambda _e: self._update_count())
        self._update_count()

        # -- settings -----------------------------------------------------
        settings = ttk.LabelFrame(outer, text="Updates", padding=10)
        settings.pack(fill=tk.X, pady=(0, 10))
        settings.columnconfigure(1, weight=1)

        ttk.Label(
            settings, text=describe_schedule(), wraplength=420, justify=tk.LEFT,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(
            settings, text="A slot missed while the machine was off is caught up on start.",
            foreground=theme.MUTED, wraplength=420, justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(2, 8))

        self.tray_var = tk.BooleanVar(value=bool(config.get("minimize_to_tray", True)))
        ttk.Checkbutton(
            settings, text="Keep running in the tray when the window is closed",
            variable=self.tray_var,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W)

        self.prune_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings, text="Delete stored history for names removed from the list",
            variable=self.prune_var,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W)

        # -- api ----------------------------------------------------------
        api = ttk.LabelFrame(outer, text="Wise Old Man API", padding=10)
        api.pack(fill=tk.X, pady=(0, 10))
        api.columnconfigure(1, weight=1)

        ttk.Label(api, text="API key (optional):").grid(row=0, column=0, sticky=tk.W)
        self.key_var = tk.StringVar(value=config.get("api_key", ""))
        ttk.Entry(api, textvariable=self.key_var, show="*").grid(
            row=0, column=1, sticky=tk.EW, padx=6)

        ttk.Label(api, text="Contact (User-Agent):").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.contact_var = tk.StringVar(value=config.get("user_agent_contact", ""))
        ttk.Entry(api, textvariable=self.contact_var).grid(
            row=1, column=1, sticky=tk.EW, padx=6, pady=(6, 0))

        ttk.Label(
            api,
            text="Without a key the API allows 20 requests a minute; a key raises it to 100.",
            foreground=theme.MUTED, wraplength=420, justify=tk.LEFT,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        # -- summaries ----------------------------------------------------
        ai = ttk.LabelFrame(outer, text="Written summaries (Claude API)", padding=10)
        ai.pack(fill=tk.X, pady=(0, 10))
        ai.columnconfigure(1, weight=1)

        self.summaries_var = tk.BooleanVar(
            value=bool(config.get("summaries_enabled", False)))
        ttk.Checkbutton(
            ai, text="Write summaries on the 6am update",
            variable=self.summaries_var,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W)

        ttk.Label(ai, text="Claude API key:").grid(row=1, column=0, sticky=tk.W,
                                                   pady=(6, 0))
        self.claude_key_var = tk.StringVar(value=config.get("anthropic_api_key", ""))
        ttk.Entry(ai, textvariable=self.claude_key_var, show="*").grid(
            row=1, column=1, sticky=tk.EW, padx=6, pady=(6, 0))

        ttk.Label(ai, text="Model:").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        self.summary_model_var = tk.StringVar(
            value=config.get("summary_model", "claude-sonnet-5"))
        ttk.Combobox(ai, textvariable=self.summary_model_var, state="readonly",
                     values=SUMMARY_MODELS).grid(
            row=2, column=1, sticky=tk.EW, padx=6, pady=(6, 0))

        ttk.Label(
            ai,
            text="The day just gone every morning, the week behind on Mondays, the "
                 "month behind on the 1st. Under a cent each on Sonnet 5. Edit the "
                 "prompt in data/summary_prompt.txt; nothing is written unless the "
                 "data moved.",
            foreground=theme.MUTED, wraplength=420, justify=tk.LEFT,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        # -- buttons ------------------------------------------------------
        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=self._save).pack(side=tk.RIGHT, padx=6)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.names_text.focus_set()
        self.grab_set()

    def _entered_names(self):
        return normalise_usernames(self.names_text.get("1.0", tk.END).splitlines())

    def _update_count(self):
        count = len(self._entered_names())
        self.count_label.config(
            text="{} name{} after removing blanks and duplicates".format(
                count, "" if count == 1 else "s"))

    def _save(self):
        names = self._entered_names()
        previous = list(self.config_obj.get("usernames", []))

        self.config_obj["usernames"] = names
        self.config_obj["minimize_to_tray"] = bool(self.tray_var.get())
        self.config_obj["api_key"] = self.key_var.get().strip()
        self.config_obj["user_agent_contact"] = self.contact_var.get().strip()
        self.config_obj["summaries_enabled"] = bool(self.summaries_var.get())
        self.config_obj["anthropic_api_key"] = self.claude_key_var.get().strip()
        self.config_obj["summary_model"] = self.summary_model_var.get()
        self.config_obj.save()

        self.result = {
            "usernames": names,
            "added": [n for n in names if n.lower() not in {p.lower() for p in previous}],
            "prune": bool(self.prune_var.get()),
        }
        if self.on_saved:
            self.on_saved(self.result)
        self.destroy()
