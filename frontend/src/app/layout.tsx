import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Shell } from "@/components/Shell";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";
import "./knowledge-workspace.css";

export const metadata: Metadata = { title: "SupportGraph", description: "Multi-agent customer support for training institutes" };
export const viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        {/* Inter (UI) and Instrument Serif (large headings). Optional: the CSS falls back to system fonts when offline. */}
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Instrument+Serif&display=swap" />
      </head>
      <body><AuthProvider><Shell>{children}</Shell></AuthProvider></body>
    </html>
  );
}
