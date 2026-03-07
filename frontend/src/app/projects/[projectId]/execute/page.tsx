'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import api from '@/lib/axios-config';

type RunStatus = 'IDLE' | 'RUNNING' | 'DONE' | 'FAILED';
type ArtifactType = 'business_plan' | 'matching' | 'roadmap';
type ArtifactFormat = 'html' | 'markdown' | 'pdf';

const ARTIFACT_TYPES: ArtifactType[] = ['business_plan', 'matching', 'roadmap'];
const ARTIFACT_FORMATS: ArtifactFormat[] = ['html', 'markdown', 'pdf'];

export default function ExecutionPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.projectId as string;

  const [status, setStatus] = useState<RunStatus>('IDLE');
  const [error, setError] = useState('');
  const [inputText, setInputText] = useState('');
  const [profileText, setProfileText] = useState(`{
  "company_name": "Sample Company",
  "years_in_business": 0,
  "annual_revenue": 0,
  "employee_count": 1,
  "item_description": "AI 기반 기업 성장지원 플랫폼",
  "has_corporation": false
}`);
  const [profileParseError, setProfileParseError] = useState('');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [loadingLatest, setLoadingLatest] = useState(false);
  const [runCount, setRunCount] = useState(0);
  const [markdownPreview, setMarkdownPreview] = useState<{ artifactType: ArtifactType; content: string } | null>(null);

  const hasArtifacts =
    status !== 'RUNNING' &&
    status !== 'FAILED' &&
    result != null &&
    typeof result.artifacts === 'object' &&
    result.artifacts !== null;
  const canOpenArtifacts = Boolean(hasArtifacts);

  const validateProfile = (value: string) => {
    try {
      JSON.parse(value);
      setProfileParseError('');
      return true;
    } catch {
      setProfileParseError('Company profile JSON format is invalid');
      return false;
    }
  };

  useEffect(() => {
    validateProfile(profileText);
  }, [profileText]);

  const buildArtifactUrl = (artifactType: ArtifactType, format: ArtifactFormat) => {
    const apiBase = api.defaults.baseURL ?? `${window.location.protocol}//${window.location.hostname}:8002/api/v1`;
    const base = apiBase.replace(/\/+$/, '');
    return `${base}/projects/${projectId}/artifacts/${artifactType}?format=${format}`;
  };

  const loadLatest = useCallback(async () => {
    setLoadingLatest(true);
    try {
      const res = await api.get(`/projects/${projectId}/growth-support/latest`);
      setResult(res.data);
      setStatus('DONE');
    } catch {
      // no latest result yet
    } finally {
      setLoadingLatest(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      loadLatest();
    }
  }, [projectId, loadLatest]);

  const runPipeline = async () => {
    if (!validateProfile(profileText)) {
      return;
    }

    setStatus('RUNNING');
    setError('');
    try {
      const profile = JSON.parse(profileText);
      const res = await api.post(`/projects/${projectId}/growth-support/run`, {
        profile,
        input_text: inputText,
      });
      setResult(res.data);
      setRunCount((v) => v + 1);
      setStatus('DONE');
    } catch (e: unknown) {
      setStatus('FAILED');
      const detail =
        typeof e === 'object' &&
        e !== null &&
        'response' in e &&
        typeof (e as { response?: { data?: { detail?: string } } }).response?.data?.detail === 'string'
          ? (e as { response: { data: { detail: string } } }).response.data.detail
          : 'Pipeline run failed';
      setError(detail);
    }
  };

  const openArtifact = async (artifactType: ArtifactType, format: ArtifactFormat) => {
    if (!projectId) return;
    if (format === 'markdown') {
      try {
        const res = await api.get(`/projects/${projectId}/artifacts/${artifactType}`, {
          params: { format: 'markdown' },
          responseType: 'text',
        });
        const raw = typeof res.data === 'string' ? res.data : String(res.data ?? '');
        const normalized = raw.includes('\\n') && !raw.includes('\n') ? raw.replace(/\\n/g, '\n') : raw;
        setMarkdownPreview({ artifactType, content: normalized });
      } catch (e) {
        setError('Failed to load markdown artifact');
      }
      return;
    }
    const url = buildArtifactUrl(artifactType, format);
    window.open(url, '_blank');
  };

  const openArtifactArea = (artifactType: ArtifactType) => {
    const artifacts = result?.artifacts as Record<string, unknown> | undefined;
    if (!artifacts || typeof artifacts[artifactType] !== 'object') return null;
    return artifacts[artifactType] as Record<string, unknown>;
  };

  return (
    <div className="h-full p-6 space-y-6 text-zinc-100">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Growth Support Execution</h1>
          <p className="text-zinc-400 text-sm">
            Run classification, business plan, matching, and roadmap in one flow.
          </p>
          <p className="text-xs text-zinc-500 mt-1" data-testid="run-count">
            Run Count: {runCount}
          </p>
        </div>
        <button
          onClick={() => router.back()}
          className="px-4 py-2 rounded bg-zinc-800 hover:bg-zinc-700"
          data-testid="back-button"
        >
          Back
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="space-y-3">
          <div className="text-sm font-semibold">Company Profile (JSON)</div>
          <textarea
            value={profileText}
            onChange={(e) => setProfileText(e.target.value)}
            className="w-full min-h-[240px] bg-zinc-950 border border-zinc-800 rounded p-3 font-mono text-xs"
            data-testid="profile-json-textarea"
          />
          {profileParseError && (
            <div className="text-red-400 text-sm" data-testid="profile-json-error">
              {profileParseError}
            </div>
          )}

          <div className="text-sm font-semibold">Existing Plan Text (optional)</div>
          <textarea
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            className="w-full min-h-[140px] bg-zinc-950 border border-zinc-800 rounded p-3 text-sm"
            placeholder="Paste existing business plan excerpt..."
            data-testid="input-textarea"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={runPipeline}
              disabled={status === 'RUNNING' || Boolean(profileParseError) || !projectId}
              className="px-4 py-2 rounded bg-green-700 hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed"
              data-testid="run-pipeline-button"
            >
              {status === 'RUNNING' ? 'Running...' : 'Run Growth Pipeline'}
            </button>
            <button
              onClick={() => {
                setStatus('IDLE');
                setError('');
              }}
              disabled={status === 'RUNNING'}
              className="px-4 py-2 rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50"
              data-testid="reset-status-button"
            >
              Reset Status
            </button>
          </div>
          {error && <div className="text-red-400 text-sm">{error}</div>}
        </div>

        <div className="space-y-4">
          <div className="border border-zinc-800 rounded p-4 bg-zinc-950">
            <div className="font-semibold mb-2">Status</div>
            <div className="text-sm" data-testid="pipeline-status">
              {status}
            </div>
          </div>

          <div className="border border-zinc-800 rounded p-4 bg-zinc-950">
            <div className="font-semibold mb-2">Artifacts</div>
            <p className="text-xs text-zinc-400 mb-3">
              Open a format to download/view each artifact type after a successful run.
            </p>
            {loadingLatest ? (
              <div className="text-zinc-400 text-sm">Loading latest artifacts...</div>
            ) : (
              <div className="space-y-4">
                {ARTIFACT_TYPES.map((artifactType) => {
                  const data = openArtifactArea(artifactType);
                  const available = !!data;
                  return (
                    <div key={artifactType} className="space-y-2">
                      <div className="text-sm text-zinc-300 capitalize">{artifactType}</div>
                      <div className="text-xs text-zinc-500">
                        {available ? 'Artifact available' : 'Not generated yet'}
                      </div>
                      <div className="flex gap-2 flex-wrap">
                        {ARTIFACT_FORMATS.map((format) => (
                          <button
                            key={`${artifactType}-${format}`}
                            onClick={() => openArtifact(artifactType, format)}
                            disabled={!canOpenArtifacts || !available}
                            className="px-3 py-1 rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm"
                            data-testid={`artifact-btn-${artifactType}-${format}`}
                          >
                            {format.toUpperCase()}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="border border-zinc-800 rounded p-4 bg-zinc-950">
            <div className="font-semibold mb-2">Latest Result Snapshot</div>
            <pre className="text-xs whitespace-pre-wrap max-h-[340px] overflow-auto">
              {result ? JSON.stringify(result, null, 2) : 'No result yet.'}
            </pre>
          </div>

          {markdownPreview && (
            <div className="border border-zinc-800 rounded p-4 bg-zinc-950">
              <div className="font-semibold mb-2">
                Markdown Preview ({markdownPreview.artifactType})
              </div>
              <div className="text-sm whitespace-normal break-words overflow-auto max-h-[420px]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {markdownPreview.content}
                </ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
