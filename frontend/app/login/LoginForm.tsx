"use client";

import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";

type LoginResult = { mfa_required?: boolean; mfa_token?: string; mfa_setup_required?: boolean };

const field = "mt-1 w-full rounded-md border border-line bg-surface px-3 py-2";

export default function LoginForm() {
  const params = useSearchParams();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<string | null>(null);
  const [useRecovery, setUseRecovery] = useState(false);

  function go(setupRequired = false) {
    const next = params.get("next");
    // full navigation on purpose: the new session cookie applies to every request from here on
    window.location.href = setupRequired ? "/security" : next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  }

  function fail(err: unknown) {
    setError(err instanceof ApiError ? err.message : "Could not reach the server. Check that the API is running.");
    setBusy(false);
  }

  async function submitPassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      const r = await api<LoginResult>("/auth/login", { method: "POST", body: JSON.stringify({ email: form.get("email"), password: form.get("password") }) });
      if (r.mfa_required && r.mfa_token) {
        setChallenge(r.mfa_token);
        setBusy(false);
        return;
      }
      go(r.mfa_setup_required);
    } catch (err) {
      fail(err);
    }
  }

  async function submitCode(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const code = String(new FormData(e.currentTarget).get("code") ?? "").trim();
    setBusy(true);
    setError("");
    try {
      await api("/auth/login/verify", { method: "POST", body: JSON.stringify({ mfa_token: challenge, code }) });
      go();
    } catch (err) {
      if (err instanceof ApiError && err.message.startsWith("Sign-in expired")) {
        setChallenge(null);
      }
      fail(err);
    }
  }

  if (challenge) {
    return (
      <form onSubmit={submitCode} className="mt-8 space-y-4" key={useRecovery ? "recovery" : "totp"}>
        <p className="text-sm">
          {useRecovery
            ? "Enter one of the recovery codes you saved when you set up two-factor authentication. Each code works once."
            : "Open your authenticator app and enter the 6-digit code for Trever RFP Portal."}
        </p>
        <label className="block">
          <span className="text-sm font-medium">{useRecovery ? "Recovery code" : "Authentication code"}</span>
          {useRecovery ? (
            <input name="code" required autoFocus autoComplete="off" spellCheck={false} placeholder="xxxx-xxxx-xxxx"
              className={`${field} num tracking-wider`} />
          ) : (
            <input name="code" required autoFocus inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}"
              maxLength={7} placeholder="123456" className={`${field} num text-lg tracking-[0.3em]`} />
          )}
        </label>
        {error && <p className="text-sm text-hot" role="alert">{error}</p>}
        <button disabled={busy} className="w-full rounded-md bg-teal py-2 font-medium text-surface disabled:opacity-60">
          {busy ? "Checking…" : "Verify and sign in"}
        </button>
        <div className="flex justify-between text-sm">
          <button type="button" className="text-teal hover:underline" onClick={() => { setUseRecovery(!useRecovery); setError(""); }}>
            {useRecovery ? "Use the authenticator app" : "Use a recovery code"}
          </button>
          <button type="button" className="text-muted hover:underline" onClick={() => { setChallenge(null); setUseRecovery(false); setError(""); }}>
            Back
          </button>
        </div>
      </form>
    );
  }

  return (
    <form onSubmit={submitPassword} className="mt-8 space-y-4">
      <label className="block">
        <span className="text-sm font-medium">Work email</span>
        <input name="email" type="email" required autoComplete="username" className={field} />
      </label>
      <label className="block">
        <span className="text-sm font-medium">Password</span>
        <input name="password" type="password" required autoComplete="current-password" className={field} />
      </label>
      {error && <p className="text-sm text-hot" role="alert">{error}</p>}
      <button disabled={busy} className="w-full rounded-md bg-teal py-2 font-medium text-surface disabled:opacity-60">
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
