"use strict";
// The sign-in form. Asks for a 6-digit code only when the account has one.
try {
  // The same theme as the dashboard in this browser (the server's choice is not public).
  let t = localStorage.getItem("sett.theme");
  if (t === "auto") t = matchMedia("(prefers-color-scheme: dark)").matches ? "midnight" : "daylight";
  if (t) document.getElementById("theme-css").href = `themes/${t}.css`;
} catch {}

addEventListener("DOMContentLoaded", () => {
  const $ = (s) => document.querySelector(s);
  const form = $("#login"), msg = $("#msg"), btn = form.querySelector("button");
  const say = (text) => { msg.textContent = text; msg.hidden = !text; };

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    say("");
    btn.disabled = true;
    try {
      const r = await fetch("api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user: $("#user").value.trim(), password: $("#password").value, code: $("#code").value }),
      });
      const d = await r.json().catch(() => ({}));
      if (d.ok) return location.replace("./");
      if (d.code) {
        $("#code-row").hidden = false;
        $("#code").required = true;
        $("#code").focus();
        say($("#code").value ? "That code didn't work. Wait for the next one and try again." : "");
        return;
      }
      $("#code").value = "";
      say(d.error || "Couldn't sign in.");
    } catch {
      say("Can't reach the dashboard server.");
    } finally {
      btn.disabled = false;
    }
  });
});
