import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Shell } from "@/components/Shell";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = { title: "SupportGraph", description: "Multi-agent customer support for training institutes" };
export const viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body><AuthProvider><Shell>{children}</Shell></AuthProvider></body>
    </html>
  );
}
