import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "AI BizPlan",
  description: "cowork AI 기반 사업계획서(인증/지원 연계)",
};

export default function AuthLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

