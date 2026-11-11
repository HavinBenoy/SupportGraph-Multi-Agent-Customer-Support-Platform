"use client";
import { useRouter } from "next/navigation";
import { LoginForm } from "@/components/LoginForm";
import { homeFor } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  return (
    <div className="auth">
      <aside className="auth-side" aria-label="About signing in">
        <p className="eyebrow">Staff &amp; administrators</p>
        <h1>Welcome back.</h1>
        <ul>
          <li>Escalations, live calls and make-up approvals</li>
          <li>The knowledge base and AI-assisted changes</li>
          <li>Analytics and trial verification (administrators)</li>
        </ul>
        <p className="muted tiny">Students do not need an account: use the chat or the voice line.</p>
      </aside>
      <div className="auth-form"><LoginForm onDone={(s) => router.push(homeFor(s.role))} /></div>
    </div>
  );
}
