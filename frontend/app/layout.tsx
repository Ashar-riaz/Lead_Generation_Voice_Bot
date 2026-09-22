import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "WTD · Lead workspace", description: "Find training prospects. Review the evidence. Send considered outreach." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
