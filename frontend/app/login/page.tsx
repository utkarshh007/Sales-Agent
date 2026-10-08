import { Suspense } from "react";
import LoginForm from "./LoginForm";

export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <h1 className="font-serif text-3xl font-semibold tracking-tight">Tender Desk</h1>
        <p className="mt-1 text-muted">Sign in to review today&apos;s cybersecurity tenders.</p>
        <Suspense>
          <LoginForm />
        </Suspense>
      </div>
    </div>
  );
}
