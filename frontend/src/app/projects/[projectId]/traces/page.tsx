'use client';

import React from 'react';
import { useParams, useRouter } from 'next/navigation';

export default function TracesPage() {
    const params = useParams();
    const router = useRouter();
    const projectId = params.projectId as string;

    // LANGFUSE_PROJECT_ID definition added back
    const LANGFUSE_PROJECT_ID = process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_ID || "your-project-id";

    const getLangfuseUrl = () => {
        const baseUrl = "https://cloud.langfuse.com";
        if (LANGFUSE_PROJECT_ID === "your-project-id") return null;
        
        // Standard URL format for Langfuse Cloud
        // project/[projectId]/traces?filter=metadata.project_id:[projectId]
        return `${baseUrl}/project/${LANGFUSE_PROJECT_ID}/traces?filter=metadata.project_id%3D${projectId}`;
    };

    const langfuseUrl = getLangfuseUrl();

    return (
        <div className="h-full flex flex-col space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-2xl font-bold">Execution Traces</h1>
                    <p className="text-zinc-400">Deep dive into agent execution traces via LangFuse.</p>
                </div>
                <div className="flex gap-3">
                    {langfuseUrl && (
                        <a
                            href={langfuseUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded transition-colors text-sm font-medium flex items-center gap-2"
                        >
                            Open in Langfuse
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                        </a>
                    )}
                    <button
                        onClick={() => router.back()}
                        className="px-4 py-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded transition-colors"
                    >
                        Back
                    </button>
                </div>
            </div>

            <div className="flex-1 bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden relative flex items-center justify-center">
                {!langfuseUrl ? (
                    <div className="flex flex-col items-center justify-center text-zinc-500">
                        <p className="mb-2">LangFuse Project ID not configured.</p>
                        <p className="text-sm">Set NEXT_PUBLIC_LANGFUSE_PROJECT_ID in .env</p>
                    </div>
                ) : (
                    <iframe
                        src={langfuseUrl}
                        className="w-full h-full border-0"
                        title="LangFuse Traces"
                        onLoad={() => console.log(`DEBUG: LangFuse Iframe URL: ${langfuseUrl}`)}
                    />
                )}
            </div>
        </div>
    );
}
