"use client";
import { useRouter } from "next/navigation";
import { LoginForm } from "@/components/LoginForm";
import { homeFor } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  return <div className="narrow"><LoginForm onDone={(s) => router.push(homeFor(s.role))} /></div>;
}
