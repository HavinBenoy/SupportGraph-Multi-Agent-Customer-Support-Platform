"use client";
import { ChatWidget } from "@/components/ChatWidget";
import { PhonePin } from "@/components/Telephony";
import { useAuth } from "@/lib/auth";

export default function ChatPage() {
  const { session } = useAuth();
  return (
    <>
      <ChatWidget />
      {session && (
        <details className="card phone-pin-card">
          <summary>Phone support PIN</summary>
          <PhonePin />
        </details>)}
    </>
  );
}
