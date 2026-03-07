import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import clsx from "clsx";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AI BizPlan",
  description: "cowork AI 기반 사업계획서(인증/지원 연계)",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={clsx(inter.className, "bg-zinc-950 text-zinc-50 antialiased")}>
        {children}
      </body>
    </html>
  );
}
