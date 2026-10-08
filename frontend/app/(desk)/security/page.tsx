"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Button, Loading, Notice, Panel } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/format";

interface MfaStatus { enabled: boolean; enabled_at: string | null; required: boolean; recovery_codes_left: number }
interface Setup { secret: string; otpauth_uri: string; qr: string; issuer: string }

const field = "mt-1 w-full rounded-md border border-line bg-surface px-3 py-2";

export default function SecurityPage() {
  const [status, setStatus] = useState<MfaStatus | null>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [mode, setMode] = useState<"idle" | "disable" | "newcodes">("idle");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<MfaStatus>("/auth/2fa").then(setStatus).catch((e) => setError(e.message));
  }, []);

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError("");
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const startSetup = () => run(async () => setSetup(await api<Setup>("/auth/2fa/setup", { method: "POST" })));

  async function confirm(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const code = String(new FormData(e.currentTarget).get("code"));
    const r = await run(() => api<MfaStatus & { recovery_codes: string[] }>("/auth/2fa/enable", { method: "POST", body: JSON.stringify({ code }) }));
    if (r) {
      setStatus(r);
      setCodes(r.recovery_codes);
      setSetup(null);
    }
  }

  async function newCodes(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const code = String(new FormData(e.currentTarget).get("code"));
    const r = await run(() => api<MfaStatus & { recovery_codes: string[] }>("/auth/2fa/recovery-codes", { method: "POST", body: JSON.stringify({ code }) }));
    if (r) {
      setStatus(r);
      setCodes(r.recovery_codes);
      setMode("idle");
    }
  }

  async function disable(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const r = await run(() => api<MfaStatus>("/auth/2fa/disable", { method: "POST", body: JSON.stringify({ password: f.get("password"), code: f.get("code") }) }));
    if (r) {
      setStatus(r);
      setMode("idle");
    }
  }

  function finishCodes() {
    setCodes(null);
    // a required-role user was held on this page until now; reload so the rest of the app opens up
    window.location.reload();
  }

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="font-serif text-3xl font-semibold tracking-tight">Security</h1>
      <p className="mt-1 text-muted">Protect your account with a second step at sign-in: a code from an authenticator app on your phone.</p>

      {status?.required && !status.enabled && (
        <div className="mt-5"><Notice tone="warn"><b>Two-factor authentication is required for your role.</b> Set it up to continue using the portal.</Notice></div>
      )}
      {error && <div className="mt-5"><Notice tone="error">{error}</Notice></div>}
      {!status && !error && <Loading />}

      {codes && (
        <div className="mt-6">
          <Panel title="Save your recovery codes">
            <p className="text-sm leading-relaxed">
              If you lose your phone, each of these codes lets you sign in once. Store them somewhere safe, such as a password manager.
              They won&apos;t be shown again.
            </p>
            <ul className="num mt-4 grid grid-cols-2 gap-x-6 gap-y-1.5 rounded-md bg-sunken px-4 py-3 font-medium tracking-wider sm:grid-cols-2">
              {codes.map((c) => <li key={c}>{c}</li>)}
            </ul>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button variant="quiet" onClick={() => navigator.clipboard?.writeText(codes.join("\n"))}>Copy codes</Button>
              <a className="rounded-md border border-line px-3.5 py-1.5 text-sm font-medium hover:bg-sunken"
                href={`data:text/plain;charset=utf-8,${encodeURIComponent(`Trever RFP Portal recovery codes\n\n${codes.join("\n")}\n`)}`}
                download="trever-recovery-codes.txt">Download as a file</a>
              <Button onClick={finishCodes}>I&apos;ve saved them</Button>
            </div>
          </Panel>
        </div>
      )}

      {status && !codes && (
        <div className="mt-6">
          <Panel title="Two-factor authentication"
            aside={<span className={`text-sm font-medium ${status.enabled ? "text-accept" : "text-muted"}`}>{status.enabled ? "On" : "Off"}</span>}>
            {!status.enabled && !setup && (
              <>
                <p className="text-sm leading-relaxed">
                  You&apos;ll need an authenticator app, such as Google Authenticator, Microsoft Authenticator or 1Password.
                  After setup, signing in asks for your password and then a 6-digit code from the app.
                </p>
                <Button className="mt-4" onClick={startSetup} disabled={busy}>Set up two-factor authentication</Button>
              </>
            )}

            {setup && (
              <ol className="space-y-5 text-sm">
                <li>
                  <p className="font-semibold">Scan this QR code with your authenticator app</p>
                  {/* eslint-disable-next-line @next/next/no-img-element -- server-generated data URI */}
                  <img src={setup.qr} alt="QR code for setting up two-factor authentication" width={180} height={180}
                    className="mt-3 rounded-md border border-line bg-white" />
                  <p className="mt-3 text-muted">Can&apos;t scan it? Enter this key in the app instead:</p>
                  <p className="num mt-1 select-all break-all rounded-md bg-sunken px-3 py-2 font-medium tracking-wider">
                    {setup.secret.match(/.{1,4}/g)?.join(" ")}
                  </p>
                </li>
                <li>
                  <form onSubmit={confirm}>
                    <label className="block font-semibold">Enter the 6-digit code the app shows
                      <input name="code" required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7}
                        placeholder="123456" className={`${field} num block max-w-[12rem] text-lg tracking-[0.3em]`} />
                    </label>
                    <div className="mt-3 flex gap-2">
                      <Button disabled={busy}>Turn on</Button>
                      <Button type="button" variant="quiet" onClick={() => setSetup(null)} disabled={busy}>Cancel</Button>
                    </div>
                  </form>
                </li>
              </ol>
            )}

            {status.enabled && (
              <>
                <dl className="space-y-2 text-sm">
                  <div><dt className="text-muted">On since</dt><dd>{date(status.enabled_at)}</dd></div>
                  <div><dt className="text-muted">Unused recovery codes</dt>
                    <dd className={status.recovery_codes_left <= 3 ? "font-semibold text-high" : ""}>
                      {status.recovery_codes_left} of 10{status.recovery_codes_left <= 3 ? ". Generate new ones soon." : ""}
                    </dd></div>
                </dl>

                {mode === "idle" && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button variant="quiet" onClick={() => setMode("newcodes")}>Generate new recovery codes</Button>
                    {!status.required && <Button variant="danger" onClick={() => setMode("disable")}>Turn off</Button>}
                  </div>
                )}
                {status.required && mode === "idle" && (
                  <p className="mt-3 text-sm text-muted">Your role requires two-factor authentication, so it can&apos;t be turned off.</p>
                )}

                {mode === "newcodes" && (
                  <form onSubmit={newCodes} className="mt-4 space-y-3 border-t border-line pt-4 text-sm">
                    <p>Your old recovery codes will stop working.</p>
                    <label className="block">Code from your authenticator app
                      <input name="code" required inputMode="numeric" autoComplete="one-time-code" maxLength={7} className={`${field} num block max-w-[12rem]`} />
                    </label>
                    <div className="flex gap-2">
                      <Button disabled={busy}>Generate new codes</Button>
                      <Button type="button" variant="quiet" onClick={() => setMode("idle")}>Cancel</Button>
                    </div>
                  </form>
                )}

                {mode === "disable" && (
                  <form onSubmit={disable} className="mt-4 space-y-3 border-t border-line pt-4 text-sm">
                    <p>Signing in will need only your password. Other signed-in browsers will be signed out.</p>
                    <label className="block">Password
                      <input name="password" type="password" required autoComplete="current-password" className={field} />
                    </label>
                    <label className="block">Code from your authenticator app
                      <input name="code" required inputMode="numeric" autoComplete="one-time-code" maxLength={7} className={`${field} num block max-w-[12rem]`} />
                    </label>
                    <div className="flex gap-2">
                      <Button variant="danger" disabled={busy}>Turn off two-factor authentication</Button>
                      <Button type="button" variant="quiet" onClick={() => setMode("idle")}>Cancel</Button>
                    </div>
                  </form>
                )}
              </>
            )}
          </Panel>
          <p className="mt-4 text-sm text-muted">
            Lost your phone and your recovery codes? Ask an administrator to reset two-factor authentication for your account.
          </p>
        </div>
      )}
    </div>
  );
}
