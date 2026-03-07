'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Send, Paperclip, FileText, Bot, User as UserIcon, Loader2, Zap, AtSign } from 'lucide-react';
import { useRouter } from 'next/navigation'; // [v4.2] Use Next.js router for proper state sync
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import api from '@/lib/axios-config';
import LogConsole from '@/components/chat/LogConsole';
import { useDomainStore } from '@/store/useDomainStore';
import { useProjectStore } from '@/store/projectStore';
import { useAuthStore } from '@/store/useAuthStore';

interface Message {
    id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    progressSteps?: string[];
    isStreaming?: boolean;
    hasLogs?: boolean;
    timestamp?: string;
    thread_id?: string;
    request_id?: string; // [v4.2] 검증용 Request ID
    disambiguateOptions?: string[];
    artifactActions?: {
        artifactType: string;
        htmlUrl?: string;
        pdfUrl?: string;
        approvalUrl?: string;
        completedSteps?: number;
        totalSteps?: number;
        missingSteps?: string[];
        missingStepGuides?: string[];
        missingFieldGuides?: string[];
    };
}

// [v4.0] Conversation Modes
type ConversationMode = 'NATURAL' | 'REQUIREMENT' | 'FUNCTION';

const MODE_CONFIG = {
    NATURAL: { label: '상담 모드', color: 'indigo', border: 'border-indigo-500', bg: 'bg-indigo-500', text: 'text-indigo-500' },
    REQUIREMENT: { label: '요건 수집', color: 'emerald', border: 'border-emerald-500', bg: 'bg-emerald-500', text: 'text-emerald-500' },
    FUNCTION: { label: '도우미', color: 'violet', border: 'border-violet-500', bg: 'bg-violet-500', text: 'text-violet-500' },
};

const stripSignalPayload = (content: string): string => {
    return content
        .replace(/\{[\s\S]*?"status"\s*:\s*"READY_TO_START"[\s\S]*?\}/g, '')
        .replace(/\{[\s\S]*?"type"\s*:\s*"DISAMBIGUATE_OPTIONS"[\s\S]*?\}/g, '')
        .replace(/\{[\s\S]*?"type"\s*:\s*"ARTIFACT_ACTIONS"[\s\S]*?\}/g, '')
        .replace(/\{[\s\S]*?"type"\s*:\s*"PIPELINE_PROGRESS"[\s\S]*?\}/g, '')
        .replace(/\{[\s\S]*?"type"\s*:\s*"MODE_SWITCH"[\s\S]*?\}/g, '')
        .trim();
};

type StreamSignal =
    | { type: 'PIPELINE_PROGRESS'; message: string }
    | { type: 'MODE_SWITCH'; mode: ConversationMode }
    | { type: 'DISAMBIGUATE_OPTIONS'; options: string[] }
    | {
        type: 'ARTIFACT_ACTIONS';
        artifact_type: string;
        html_url?: string;
        pdf_url?: string;
        approval_url?: string;
        completed_steps?: number;
        total_steps?: number;
        missing_steps?: string[];
        missing_step_guides?: string[];
        missing_field_guides?: string[];
    }
    | { type: 'READY_TO_START'; final_summary: string };

const isConversationMode = (value: unknown): value is ConversationMode =>
    value === 'NATURAL' || value === 'REQUIREMENT' || value === 'FUNCTION';

const findJsonObjectEnd = (input: string, startIndex: number): number => {
    let depth = 0;
    let inString = false;
    let escaped = false;

    for (let i = startIndex; i < input.length; i += 1) {
        const char = input[i];

        if (inString) {
            if (escaped) {
                escaped = false;
                continue;
            }
            if (char === '\\') {
                escaped = true;
                continue;
            }
            if (char === '"') {
                inString = false;
            }
            continue;
        }

        if (char === '"') {
            inString = true;
            continue;
        }

        if (char === '{') {
            depth += 1;
            continue;
        }

        if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return i;
            }
        }
    }

    return -1;
};

const parseStreamSignal = (raw: unknown): StreamSignal | null => {
    if (!raw || typeof raw !== 'object') return null;
    const obj = raw as Record<string, unknown>;
    const rawType = typeof obj.type === 'string' ? obj.type : undefined;

    if (rawType === 'PIPELINE_PROGRESS' && typeof obj.message === 'string') {
        return { type: 'PIPELINE_PROGRESS', message: obj.message };
    }
    if (rawType === 'MODE_SWITCH' && isConversationMode(obj.mode)) {
        return { type: 'MODE_SWITCH', mode: obj.mode };
    }
    if (rawType === 'DISAMBIGUATE_OPTIONS' && Array.isArray(obj.options)) {
        return {
            type: 'DISAMBIGUATE_OPTIONS',
            options: obj.options.map((item) => String(item)),
        };
    }
    if (rawType === 'ARTIFACT_ACTIONS') {
        return {
            type: 'ARTIFACT_ACTIONS',
            artifact_type: String(obj.artifact_type || ''),
            html_url: typeof obj.html_url === 'string' ? obj.html_url : undefined,
            pdf_url: typeof obj.pdf_url === 'string' ? obj.pdf_url : undefined,
            approval_url: typeof obj.approval_url === 'string' ? obj.approval_url : undefined,
            completed_steps: typeof obj.completed_steps === 'number' ? obj.completed_steps : undefined,
            total_steps: typeof obj.total_steps === 'number' ? obj.total_steps : undefined,
            missing_steps: Array.isArray(obj.missing_steps) ? obj.missing_steps.map((item) => String(item)) : [],
            missing_step_guides: Array.isArray(obj.missing_step_guides) ? obj.missing_step_guides.map((item) => String(item)) : [],
            missing_field_guides: Array.isArray(obj.missing_field_guides) ? obj.missing_field_guides.map((item) => String(item)) : [],
        };
    }
    if (obj.status === 'READY_TO_START') {
        return {
            type: 'READY_TO_START',
            final_summary: typeof obj.final_summary === 'string' ? obj.final_summary : '',
        };
    }

    return null;
};

const extractSignalsFromBuffer = (buffer: string): { text: string; carry: string; signals: StreamSignal[] } => {
    let cursor = 0;
    let lastConsumed = 0;
    let text = '';
    const signals: StreamSignal[] = [];

    while (cursor < buffer.length) {
        const openIndex = buffer.indexOf('{', cursor);
        if (openIndex === -1) break;

        text += buffer.slice(lastConsumed, openIndex);
        const lookahead = buffer.slice(openIndex, Math.min(buffer.length, openIndex + 120));
        const isLikelySignal = lookahead.includes('"type"') || lookahead.includes('"status"');
        if (!isLikelySignal) {
            text += '{';
            cursor = openIndex + 1;
            lastConsumed = cursor;
            continue;
        }

        const closeIndex = findJsonObjectEnd(buffer, openIndex);
        if (closeIndex === -1) {
            return { text, carry: buffer.slice(openIndex), signals };
        }

        const candidate = buffer.slice(openIndex, closeIndex + 1);
        try {
            const parsed = JSON.parse(candidate);
            const signal = parseStreamSignal(parsed);
            if (signal) {
                signals.push(signal);
            } else {
                text += candidate;
            }
        } catch {
            text += candidate;
        }

        cursor = closeIndex + 1;
        lastConsumed = cursor;
    }

    text += buffer.slice(lastConsumed);
    return { text, carry: '', signals };
};

const getProgressPercent = (steps?: string[]) => {
    const count = Array.isArray(steps) ? steps.length : 0;
    if (count <= 0) return 15;
    return Math.min(95, 15 + count * 20);
};

const APPROVAL_STEP_LABELS: Record<string, string> = {
    key_figures_approved: '핵심 수치 확인',
    certification_path_approved: '인증 방향 확인',
    template_selected: '템플릿 선택 확인',
    summary_confirmed: '요약본 확인',
};

const APPROVAL_STEP_GUIDES: Record<string, string> = {
    key_figures_approved: '매출/비용/자금 최신값을 알려주세요.',
    certification_path_approved: '인증/지원 방향(충족·미충족·추가확인)을 알려주세요.',
    template_selected: '원하는 양식(템플릿)을 알려주세요.',
    summary_confirmed: '요약본 내용을 확인해 주세요. 맞으면 확정, 아니면 수정 요청해 주세요.',
};

const renderMarkdownMessageContent = (content: string): React.ReactNode => {
    const sanitized = stripSignalPayload(content || '');
    if (!sanitized) return null;
    const normalized = sanitized.includes('\\n') && !sanitized.includes('\n')
        ? sanitized.replace(/\\n/g, '\n')
        : sanitized;

    return (
        <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
                a: ({ ...props }) => (
                    <a
                        {...props}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-400 underline underline-offset-2 hover:text-indigo-300"
                    />
                ),
                table: ({ ...props }) => (
                    <table {...props} className="mt-2 mb-2 w-full border-collapse text-xs" />
                ),
                thead: ({ ...props }) => (
                    <thead {...props} className="bg-zinc-800/60" />
                ),
                th: ({ ...props }) => (
                    <th {...props} className="border border-zinc-700 px-2 py-1 text-left font-semibold text-zinc-200" />
                ),
                td: ({ ...props }) => (
                    <td {...props} className="border border-zinc-700 px-2 py-1 align-top text-zinc-300" />
                ),
                p: ({ ...props }) => (
                    <p {...props} className="mb-2 last:mb-0" />
                ),
                ul: ({ ...props }) => (
                    <ul {...props} className="list-disc pl-4 mb-2 last:mb-0" />
                ),
                ol: ({ ...props }) => (
                    <ol {...props} className="list-decimal pl-4 mb-2 last:mb-0" />
                ),
            }}
        >
            {normalized}
        </ReactMarkdown>
    );
};

const KEY_FIGURES_AUTO_APPROVAL_HINTS = [
    '매출',
    '비용',
    '자금',
    '투자',
    '손익',
    '영업이익',
    '자금조달',
];

const CERTIFICATION_AUTO_APPROVAL_HINTS = [
    '인증',
    '지원',
    '충족',
    '미충족',
    '지재권',
    '특허',
    '확인',
];

const GENERIC_APPROVAL_CONFIRM_HINTS = [
    '예',
    '네',
    '응',
    '맞아',
    '맞습니다',
    '맞아요',
    '확인',
    '승인',
    '진행',
    '완료',
    'ok',
    'okay',
    'yes',
];

const CONTINUE_RETRY_HINTS = [
    '이어서',
    '이어',
    '계속',
    'resume',
    'retry',
    '다시',
];

const DEFAULT_WELCOME_MESSAGES = {
    first_login: `안녕하세요, AIBizPlan에 오신 걸 환영해요. 😊
첫 상담이라도 전혀 부담 가지지 마세요.
AI가 대신 판단하지 않고, 지금은 '질문 중심'으로 차분하게 도와드리는 방식이에요.

처음 오신 분을 위해 가장 안전한 순서를 먼저 안내드릴게요.
1) 회사/사업의 핵심 정보부터 정리 (회사명, 업종, 제품/서비스, 고객)
2) 매출/운영 현황, 지원 필요사항을 단계적으로 질문
3) 지금까지의 답변으로 적절한 사업계획서 템플릿 추천
4) 초안 작성 → 점검/보완 → 최종 승인 후 PDF 생성

지금은 긴 문장을 몰라도 괜찮아요. 한 문장씩 가볍게 입력해도 충분합니다.
예: “회사명은 OO입니다”, “아직 잘 몰라서 천천히 설명해줘요”처럼 말씀해 주세요.
기본적으로 저와의 대화는 저장되고 다음 단계로 자연스럽게 이어집니다.`,
    room_ready: `이 상담방은 “새로운 상담방”으로 시작된 현재 프로젝트 전용 작업공간입니다.
방 안에서 주고받은 내용은 해당 상담 내용으로만 이어져서 반영돼요.

처음엔 복잡한 항목을 한 번에 채우지 않아도 괜찮습니다.
알고 있는 내용부터 짧게 말해 주세요.
예: “회사명은 …”, “우리 제품은 …”, “고객은 …”, “매출은 …”처럼 말해주세요.

제가 필요할 때 필요한 질문만 드릴게요.
중간에 “지금까지 내용 요약해줘”라고 하면 한 번에 정리해드릴 수 있어요.`,
};

const MODE_TOGGLE_MESSAGES: Record<ConversationMode, string> = {
    NATURAL: `상담 모드로 전환했어요.

공통 사용법:
- 초보자도 바로 시작할 수 있게 한 문장씩 천천히 말해도 됩니다.
- “무슨 내용이 필요한지 모르겠다”면 “요약해줘” 또는 “다음엔 무엇을 물어야 해?”라고 물어보세요.
- 언제든 “지금까지 내용 요약해줘”로 지금까지 수집된 내용을 정리받을 수 있어요.

현재 모드에서 하는 일:
- 사업계획서 생성을 위한 핵심정보를 수집해요.
  (회사/사업/고객/시장/매출/자금 등)
- 수집된 정보 기준으로 예비/초기/성장 단계를 판단해요.
- 적절한 템플릿 추천 근거를 만들고, 초안 작성에 필요한 다음 질문을 제시해요.
- 모호하면 “천천히 정리” 흐름으로 진행해요.`,
    REQUIREMENT: `요건 수집 모드로 전환했어요.

공통 사용법:
- 지원사업 신청 전, 현재 상태를 빠르게 점검하는 모드예요.
- “요건 체크해줘”라고 한 번 말하면 체크리스트형으로 정리해줘요.
- 증빙이 불명확한 항목은 질문을 통해 보완할 수 있어요.

현재 모드에서 하는 일:
- 지원사업 신청 요건(매출, 근로, 고용, 기간, 증빙) 충족 여부를 점검해요.
- 누락/미흡 항목을 필수/권장으로 분류해 우선순위를 제시해요.
- 제출용 체크포인트를 한 번에 볼 수 있게 정리해줘요.`,
    FUNCTION: `도우미 모드로 전환했어요.

공통 사용법:
- 문장 다듬기/요약/표현 보완에 최적화된 보조 모드예요.
- “문장 다듬어줘”, “짧게 요약해줘”, “공식 톤으로 바꿔줘”처럼 바로 요청하세요.
- 원문은 유지하거나, 핵심만 압축하는 방식으로 편집해요.

현재 모드에서 하는 일:
- 사업계획서 문장, 문단, 항목 제목을 깔끔하게 정리해요.
- 지원기관 제출에 맞는 표현 톤으로 바꿔줘요.
- 표/항목 형식 문구를 보기 좋게 다듬어줘요.`,
};

interface ChatInterfaceProps {
    projectId?: string;
    threadId?: string;
}

// [v4.2 UX] MessageAuditBar Component for Lazy Loading Stats
function MessageAuditBar({ requestId, projectId, onTabChange }: { requestId: string, projectId?: string, onTabChange: (tab: string, reqId: string, nodeId?: string) => void }) {
    const [stats, setStats] = useState<{ topScore: number | string, chunkCount: number, nodeCount: number | string, topNodeId?: string } | null>(null);
    const [loading, setLoading] = useState(true);
    const { token } = useAuthStore();

    useEffect(() => {
        let isMounted = true;
        let retryCount = 0;
        
        const fetchStats = async () => {
            try {
                // Use Query Param
                const response = await api.get(`/master/chat_debug`, {
                    params: { request_id: requestId },
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                
                // [v5.0] Retry logic for 404 Race Condition
                // If 200 but empty/null, it might be partial. But if 404, axios throws.
                const chunks = response.data.debug_info.retrieval.chunks || [];
                const topScore = chunks.length > 0 ? Math.max(...chunks.map((c: any) => c.score)).toFixed(4) : '-';
                const topNodeId = chunks.length > 0 ? chunks[0].node_id : undefined; // [v5.0] Top chunk's node_id

                // [v5.0 DEBUG] Log node_id extraction
                if (chunks.length > 0) {
                    console.log(`[v5.0 MessageAuditBar] Top chunk node_id: ${topNodeId}, title: ${chunks[0].title?.substring(0, 30)}`);
                }

                if (isMounted) {
                    setStats({
                        topScore,
                        chunkCount: chunks.length,
                        nodeCount: '-',
                        topNodeId
                    });
                    setLoading(false);
                }
            } catch (err: any) {
                // [v5.0] Retry if 404 (Data might be syncing)
                if (err.response?.status === 404 && retryCount < 2) {
                    retryCount++;
                    setTimeout(fetchStats, 1500); // Wait 1.5s and retry
                } else {
                    if (isMounted) {
                        setStats(null);
                        setLoading(false);
                    }
                }
            }
        };
        
        // Initial delay to allow backend to persist
        setTimeout(fetchStats, 1000);
        
        return () => { isMounted = false; };
    }, [requestId, token]);

    if (loading) return <div className="mt-2 pt-2 border-t border-zinc-800/50 text-[10px] text-zinc-600 animate-pulse">Verifying sources...</div>;
    if (!stats) return null; // Hide if no data or error

    return (
        <div className="mt-2 pt-2 border-t border-zinc-800/50 flex items-center gap-3 text-[10px] font-mono text-zinc-500 whitespace-nowrap overflow-hidden text-ellipsis">
            <span className="uppercase tracking-wider font-bold text-zinc-600">출처</span>
            <span>·</span>
            <span title={`Top Similarity Score: ${stats.topScore}`}>Top1 {stats.topScore}</span>
            <span>·</span>
            <span title={`Retrieved Chunks: ${stats.chunkCount}`}>Chunks {stats.chunkCount}</span>
            <span>·</span>
            <span title="Graph Nodes Linked">Nodes {stats.nodeCount}</span>

            <button
                onClick={() => {
                    console.log(`[v5.0 Vector Button] Navigating with nodeId: ${stats.topNodeId}`);
                    onTabChange('vector', requestId, stats.topNodeId);
                }}
                className="ml-2 text-indigo-400 hover:text-indigo-300 hover:underline"
                title={stats.topNodeId ? `Navigate to node: ${stats.topNodeId}` : 'View vector map'}
            >
                [Vector]
            </button>
            <button
                onClick={() => {
                    console.log(`[v5.0 Graph Button] Navigating with nodeId: ${stats.topNodeId}`);
                    onTabChange('graph', requestId, stats.topNodeId);
                }}
                className="text-emerald-400 hover:text-emerald-300 hover:underline"
                title={stats.topNodeId ? `Navigate to node: ${stats.topNodeId}` : 'View knowledge graph'}
            >
                [Graph]
            </button>
        </div>
    );
}

export default function ChatInterface({ projectId: propProjectId, threadId }: ChatInterfaceProps) {
    const {
        currentProjectId,
        setCurrentProjectId,
        projects,
        setProjects,
        currentThreadId: storeCurrentThreadId,
        setCurrentThreadId: setStoreCurrentThreadId,
    } = useProjectStore();
    const { user } = useAuthStore(); // [v4.2] user role preserved for role-based UI labels if needed
    const projectId = (propProjectId || currentProjectId) || undefined;
    const isAdmin = user?.role === 'super_admin' || user?.role === 'tenant_admin';
    const defaultThreadTitle = `${user?.username || 'user'}_talk`;
    const resolveProjectId = (value?: string | null) => {
        if (!value) return null;
        return (!isAdmin && value === 'system-master') ? null : value;
    };
    const effectiveProjectId = isAdmin ? (projectId || 'system-master') : resolveProjectId(projectId);

    // [v4.2] Router for tab switching
    const router = useRouter();

    const [input, setInput] = useState('');
    const [messages, setMessages] = useState<Message[]>([]);
    const [loading, setLoading] = useState(false);
    const [showLogs, setShowLogs] = useState(false);
    const [taskStarted, setTaskStarted] = useState(false);
    const [logs, setLogs] = useState<string[]>([]);
    const [socket, setSocket] = useState<WebSocket | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [isUploading, setIsUploading] = useState(false);
    const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
    const [uploadNotice, setUploadNotice] = useState<{ type: 'success' | 'error' | 'partial'; text: string } | null>(null);
    const [contextGuardMessage, setContextGuardMessage] = useState<string | null>(null);

    // [v4.0] Conversation Mode State
    const [mode, setMode] = useState<ConversationMode>('NATURAL');
    const [modeChangeOrigin, setModeChangeOrigin] = useState<'auto' | 'user'>('auto');
    const [showModeMenu, setShowModeMenu] = useState(false);
    const getWelcomeMessage = (targetProjectId?: string) => {
        return targetProjectId ? DEFAULT_WELCOME_MESSAGES.room_ready : DEFAULT_WELCOME_MESSAGES.first_login;
    };

    // WebSocket for real-time logs
    useEffect(() => {
        if (taskStarted && projectId && !socket) {
            const currentHostname = (window.location.hostname === 'localhost' || window.location.hostname === '0.0.0.0')
                ? '127.0.0.1'
                : window.location.hostname;
            const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${wsProtocol}//${currentHostname}:8002/api/v1/orchestration/ws/${projectId}`;

            // console.log(`DEBUG: Connecting to WebSocket: ${wsUrl}`);
            const newSocket = new WebSocket(wsUrl);

            newSocket.onopen = () => {
                // console.log("DEBUG: WebSocket Connected");
                setLogs(prev => [...prev, "📡 실시간 로그 연결 성공"]);
            };

            newSocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    // console.log("DEBUG: WS Message", data);
                    if (data.data?.message) {
                        setLogs(prev => [...prev, data.data.message]);
                    }
                    if (data.type === 'WORKFLOW_FINISHED' || data.type === 'WORKFLOW_FAILED') {
                        // [Fix] Unlock UI to allow re-triggering START TASK after finish or failure
                        setTaskStarted(false);
                        setReadyToStart(false);
                        // console.log(`DEBUG: Workflow ${data.type} - UI Unlocked`);
                    }
                } catch (e) {
                    console.error("Failed to parse WS message", e);
                    setLogs(prev => [...prev, event.data]);
                }
            };

            newSocket.onclose = () => {
                // console.log("DEBUG: WebSocket Disconnected");
                setSocket(null);
            };

            setSocket(newSocket);
        }

        return () => {
            if (socket) {
                socket.close();
            }
        };
    }, [taskStarted, projectId]);
    const [limit, setLimit] = useState(20);
    const [hasMore, setHasMore] = useState(true);

    // [Fix] Thread ID Management
    const [currentThreadId, setCurrentThreadId] = useState<string | undefined>(threadId || undefined);
    const isValidThreadId = (value?: string | null): value is string => {
        return typeof value === 'string' && value !== 'null' && value !== 'undefined' && value.trim().length > 0;
    };
    const resolvedThreadId = isValidThreadId(threadId) ? threadId : undefined;
    const activeThreadId = resolvedThreadId || currentThreadId;
    const visibleThreadId = resolvedThreadId || currentThreadId;
    const threadFetchSeq = useRef(0);
    const buildThreadPayloadFromMessages = (targetThreadId: string) => {
        return messages
            .filter((m) => m.thread_id === targetThreadId)
            .map((m) => ({ role: m.role, content: m.content }));
    };

    useEffect(() => {
        if (resolvedThreadId && resolvedThreadId !== currentThreadId) {
            setCurrentThreadId(resolvedThreadId);
        }
        if (resolvedThreadId && resolvedThreadId !== storeCurrentThreadId) {
            setStoreCurrentThreadId(resolvedThreadId);
        }
    }, [resolvedThreadId, currentThreadId, storeCurrentThreadId, setStoreCurrentThreadId]);

    useEffect(() => {
        setMode('NATURAL');
        setModeChangeOrigin('auto');
    }, [resolvedThreadId, projectId]);

    // START TASK Gate State
    const [readyToStart, setReadyToStart] = useState(false);
    const [finalSummary, setFinalSummary] = useState('');

    // Mention State
    const [showMentions, setShowMentions] = useState(false);
    const [mentionSearch, setMentionSearch] = useState('');
    const [cursorPos, setCursorPos] = useState(0);

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const pendingRetryRef = useRef<{ threadId?: string; requestText: string } | null>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const currentDomain = useDomainStore((state) => state.currentDomain);
    const activeProject = projects.find(p => p.id === projectId);
    const provisioningRef = useRef(false);

    // [v5.0] Multi File Upload Handler
    const uploadSelectedFiles = async (files: File[]) => {
        if (files.length === 0) return;
        if (!effectiveProjectId) {
            setUploadNotice({
                type: 'error',
                text: '상담방을 준비 중입니다. 잠시 후 다시 시도해 주세요.'
            });
            return;
        }

        setIsUploading(true);
        setUploadNotice(null);
        try {
            const results = await Promise.allSettled(
                files.map(async (file) => {
                    const formData = new FormData();
                    formData.append('file', file);
                    formData.append('project_id', effectiveProjectId);
                    return api.post('/files/upload', formData, {
                        headers: { 'Content-Type': 'multipart/form-data' }
                    }).then((response) => ({
                        fileName: file.name,
                        fileId: response.data?.file_id || response.data?.id || response.data?.data?.file_id
                    }));
                })
            );

            const uploaded = results
                .map((r) => (r.status === 'fulfilled' ? r.value : null))
                .filter((r): r is { fileName: string; fileId: string } => r !== null);
            const failedCount = results.length - uploaded.length;

            const summary = [
                uploaded.length > 0 ? `첨부파일 업로드 완료: ${uploaded.length}개` : null,
                ...uploaded.map((item) => `- ${item.fileName} (ID: ${item.fileId})`),
                failedCount > 0 ? `업로드 실패: ${failedCount}개` : null
            ].filter(Boolean).join('\n');

            const type: 'success' | 'error' | 'partial' =
                uploaded.length === 0 ? 'error' : (failedCount > 0 ? 'partial' : 'success');
            setUploadNotice({
                type,
                text: summary || '업로드된 파일이 없습니다.'
            });

            setMessages(prev => [...prev, {
                id: Date.now().toString(),
                role: 'assistant',
                content: summary || '⚠️ 첨부된 파일이 없습니다.'
            }]);
        } catch (err: any) {
            console.error("File upload failed", err);
            setUploadNotice({
                type: 'error',
                text: `업로드 실패: ${err.response?.data?.detail || err.message}`
            });
            setMessages(prev => [...prev, {
                id: Date.now().toString(),
                role: 'assistant',
                content: `첨부파일 업로드 실패: ${err.response?.data?.detail || err.message}`
            }]);
        } finally {
            setIsUploading(false);
            setSelectedFiles([]);
            if (fileInputRef.current) fileInputRef.current.value = '';
        }
    };

    const formatFileSize = (size: number) => {
        if (size < 1024) return `${size} B`;
        if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
        return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    };

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        if (!e.target.files || e.target.files.length === 0) return;

        const files = Array.from(e.target.files);
        setSelectedFiles(files);
        await uploadSelectedFiles(files);
    };

    const pushModeGuideMessage = (nextMode: ConversationMode) => {
        const label = MODE_CONFIG[nextMode].label;
        const content = `【모드 전환】 ${label}\n\n${MODE_TOGGLE_MESSAGES[nextMode]}`;

        setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.role === 'assistant' && last.content === content) {
                return prev;
            }
            return [
                ...prev,
                {
                    id: Date.now().toString(),
                    role: 'assistant',
                    content,
                    thread_id: resolvedThreadId || currentThreadId,
                },
            ];
        });
    };

    const sendDisambiguateOption = async (optionText: string) => {
        if (loading) return;
        // 즉시 해당 선택지 메시지를 제거해 중복 클릭을 줄임
        setMessages(prev => prev.map(msg =>
            msg.content.includes('원하시는 흐름 하나를 골라주세요') && msg.disambiguateOptions
                ? { ...msg, disambiguateOptions: undefined }
                : msg
        ));
        await handleSend('chat', optionText);
    };

    useEffect(() => {
        const shouldRenderWelcome = messages.length === 0 && !loading && !contextGuardMessage;
        if (!shouldRenderWelcome) {
            return;
        }

        setMessages([{
            id: `welcome-${Date.now()}`,
            role: 'assistant',
            content: getWelcomeMessage(projectId),
                    thread_id: resolvedThreadId || currentThreadId,
        }]);
    }, [messages.length, loading, projectId, activeThreadId, contextGuardMessage]);

    // [Fix] 메시지 목록이 바뀔 때마다 마지막 메시지에서 READY_TO_START 신호를 찾아 버튼을 복구합니다.
    useEffect(() => {
        if (messages.length > 0) {
            const lastMsg = messages[messages.length - 1];
            if (lastMsg.role === 'assistant') {
                const jsonMatch = lastMsg.content.match(/\{[\s\S]*?"status"\s*:\s*"READY_TO_START"[\s\S]*?\}/);
                if (jsonMatch) {
                    try {
                        const signal = JSON.parse(jsonMatch[0]);
                        setReadyToStart(true);
                        setFinalSummary(signal.final_summary);
                    } catch (e) {
                        console.error("Failed to parse existing signal", e);
                    }
                }
            }
        }
    }, [messages]);

    const scrollToBottom = (behavior: ScrollBehavior = 'smooth') => {
        messagesEndRef.current?.scrollIntoView({ behavior, block: 'nearest' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    useEffect(() => {
        if (isAdmin) {
            setContextGuardMessage(null);
            return;
        }

        if (!projectId) {
            const hasUsableProject = projects.some(project => project.id !== 'system-master');
            if (!hasUsableProject) {
                if (provisioningRef.current) return;
                provisioningRef.current = true;
                const provisionDefaultWorkspace = async () => {
                    try {
                        const defaultProjectName = `${user?.username || 'user'}_talk`;
                        const projectRes = await api.post('/projects/', {
                            name: defaultProjectName,
                            project_type: 'GROWTH_SUPPORT',
                        });
                        const createdProjectId = projectRes.data.id;
                        setCurrentProjectId(createdProjectId);
                        const threadRes = await api.post(`/projects/${createdProjectId}/threads`, {
                            title: defaultProjectName
                        });
                        const threadIdForInit = threadRes.data.thread_id;
                        setContextGuardMessage(null);
                        router.replace(`/chat?projectId=${createdProjectId}&threadId=${threadIdForInit}`);
                    } catch (err) {
                        console.error('[ChatInterface] Failed to bootstrap default project/thread', err);
                        setContextGuardMessage('상담 프로젝트를 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.');
                        setCurrentProjectId(null);
                    } finally {
                        provisioningRef.current = false;
                    }
                };

                provisionDefaultWorkspace();
                return;
            }

            const fallbackProjectId = currentProjectId && currentProjectId !== 'system-master'
                ? currentProjectId
                : projects.find(project => project.id !== 'system-master')?.id;

            if (fallbackProjectId) {
                setCurrentProjectId(fallbackProjectId);
                setContextGuardMessage(null);
                if (fallbackProjectId !== currentProjectId) {
                    router.replace(`/chat?projectId=${fallbackProjectId}`);
                }
            }

            return;
        }

        if (projectId === 'system-master') {
            const usableProjectId = projects.find(project => project.id !== 'system-master')?.id;
            if (usableProjectId) {
                setCurrentProjectId(usableProjectId);
                router.replace(`/chat?projectId=${usableProjectId}`);
                return;
            }
            setContextGuardMessage('상담방 준비 중입니다. 권한 제한 projectId는 사용할 수 없습니다.');
            return;
        }

        setContextGuardMessage(null);
    }, [isAdmin, projectId, projects, currentProjectId, setCurrentProjectId, router, user?.username]);

    const resolveAndSetThread = async (targetProjectId: string, requestedThreadId?: string) => {
        const normalizedRequestedThreadId = isValidThreadId(requestedThreadId) ? requestedThreadId : undefined;

        // 1) 요청된 스레드 우선 확인: 존재하면 즉시 해당 스레드로 고정
        if (normalizedRequestedThreadId) {
            try {
                const historyRes = await api.get(
                    `/projects/${targetProjectId}/threads/${normalizedRequestedThreadId}/messages`,
                    { params: { limit: 1 } }
                );
                if (historyRes.status === 200) {
                    setCurrentThreadId(normalizedRequestedThreadId);
                    if (normalizedRequestedThreadId !== storeCurrentThreadId) {
                        setStoreCurrentThreadId(normalizedRequestedThreadId);
                    }
                    return normalizedRequestedThreadId;
                }
            } catch (error: any) {
                if (error?.response?.status === 404) {
                    console.warn('[ChatInterface] Requested thread not found.', normalizedRequestedThreadId);
                } else {
                    console.warn('[ChatInterface] Requested thread validation failed.', error);
                }
                // 요청 스레드가 유효하지 않으면 생성·이동하지 않고 호출부가 메시지로 처리.
                return undefined;
            }
        }

        try {
            const threadsRes = await api.get(`/projects/${targetProjectId}/threads`);
            const threadList = Array.isArray(threadsRes.data) ? threadsRes.data : [];
            const pickedThreadId = threadList[0]?.thread_id;

            if (pickedThreadId) {
                setCurrentThreadId(pickedThreadId);
                setStoreCurrentThreadId(pickedThreadId);
                if (!normalizedRequestedThreadId) {
                    router.replace(`/chat?projectId=${targetProjectId}&threadId=${pickedThreadId}`);
                }
                await fetchHistory(20, pickedThreadId);
                return pickedThreadId;
            }

            const createRes = await api.post(`/projects/${targetProjectId}/threads`, {
                title: defaultThreadTitle,
            });
            const newThreadId = createRes.data?.thread_id;
            if (newThreadId) {
                setCurrentThreadId(newThreadId);
                setStoreCurrentThreadId(newThreadId);
                setMessages([]);
                router.replace(`/chat?projectId=${targetProjectId}&threadId=${newThreadId}`);
                return newThreadId;
            }
        } catch (error) {
            console.error('[ChatInterface] resolveAndSetThread failed', error);
        }

        return null;
    };

    // [v5.0] State Cleanup and Initialization on Project Change
    useEffect(() => {
        const initChat = async () => {
            // Reset state on project change
            setMessages([]);
            setLogs([]);
            setReadyToStart(false);
            setHasMore(true);
            
            if (!projectId || (projectId === 'system-master' && !isAdmin)) {
                return;
            }

            const desiredThreadId = resolvedThreadId;

            // If projectId changed, we must ensure threadId is valid or fetch default
            if (projectId) {
                const effectiveThreadId = await resolveAndSetThread(projectId, desiredThreadId);
                if (!effectiveThreadId) {
                    setContextGuardMessage('요청한 상담방을 찾지 못했습니다. 상담방 목록의 최근 방으로 이동합니다.');
                    const threadsRes = await api.get(`/projects/${projectId}/threads`);
                    const threads = Array.isArray(threadsRes.data) ? threadsRes.data : [];
                    const fallbackThreadId = threads?.[0]?.thread_id;
                    if (fallbackThreadId) {
                        setCurrentThreadId(fallbackThreadId);
                        setStoreCurrentThreadId(fallbackThreadId);
                        router.replace(`/chat?projectId=${projectId}&threadId=${fallbackThreadId}`);
                        await fetchHistory(20, fallbackThreadId);
                    }
                    return;
                }

                setCurrentThreadId(effectiveThreadId);
                setStoreCurrentThreadId(effectiveThreadId);
                setReadyToStart(false);
                setFinalSummary('');
                await fetchHistory(20, effectiveThreadId);
            }
        };
        
        initChat();
    }, [projectId, resolvedThreadId, defaultThreadTitle, isAdmin]);

    // Hard guard: whenever URL thread changes, force-refresh that thread history.
    // This prevents stale in-memory state from showing previous room messages.
    useEffect(() => {
        if (!projectId || !resolvedThreadId) {
            return;
        }
        setMessages([]);
        setReadyToStart(false);
        setFinalSummary('');
        fetchHistory(20, resolvedThreadId);
    }, [projectId, resolvedThreadId]);

    const fetchHistory = async (currentLimit: number, specificThreadId?: string) => {
        if (!projectId) return;
        
        // Use provided threadId or fall back to state (but state might be stale in useEffect)
        // So we prefer explicit argument
        const targetThreadId = specificThreadId || resolvedThreadId || currentThreadId;
        const requestSeq = ++threadFetchSeq.current;
        
        if (!targetThreadId) {
            console.warn("Skipping fetchHistory: No threadId available");
            console.error("DEBUG: [Audit] Race Condition Detected - fetchHistory called without threadId. Current State:", { projectId, currentThreadId, specificThreadId });
            return;
        }

        console.log(`DEBUG: [History] Fetching messages for Thread: ${targetThreadId} in Project: ${projectId}`);
        console.log(`DEBUG: [Audit] Requesting GET /projects/${projectId}/threads/${targetThreadId}/messages`);

        try {
            // [Fix] Use dedicated thread message endpoint
            const response = await api.get(`/projects/${projectId}/threads/${targetThreadId}/messages`, { 
                params: { limit: currentLimit, _ts: Date.now() },
                headers: {
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    Pragma: "no-cache",
                    Expires: "0",
                },
            });

            if (requestSeq !== threadFetchSeq.current) {
                return;
            }

            // [Audit] Log Raw Response for Data Mapping Check
            console.log("DEBUG: [History] Raw API Response:", response.data);

            const historyMessages: Message[] = response.data.map((msg: any) => {
                const hydrated = hydrateMessageFromRaw(msg.content || '');
                return {
                    id: msg.id || Math.random().toString(),
                    role: msg.role,
                    content: hydrated.content,
                    disambiguateOptions: hydrated.disambiguateOptions,
                    artifactActions: hydrated.artifactActions,
                    progressSteps: hydrated.progressSteps,
                    timestamp: msg.created_at,
                    thread_id: msg.thread_id,
                    request_id: msg.request_id, // Ensure request_id is passed for audit bar
                };
            });

            setMessages(historyMessages);
            setHasMore(response.data.length === currentLimit);
        } catch (error: any) {
            if (requestSeq !== threadFetchSeq.current) {
                return;
            }
            console.error("Failed to fetch chat history", error);
            if (error.response?.status === 404) {
                console.warn("Project or Thread not found. Resetting context.");
                // Handle 404 gracefully?
            }
        }
    };

    const handleLoadMore = () => {
        const newLimit = limit + 20;
        setLimit(newLimit);
        fetchHistory(newLimit);
    };

    const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const value = e.target.value;
        const position = e.target.selectionStart;
        setInput(value);
        setCursorPos(position);

        // Check for '@' mention
        const lastAtPos = value.lastIndexOf('@', position - 1);
        if (lastAtPos !== -1 && (lastAtPos === 0 || value[lastAtPos - 1] === ' ')) {
            const query = value.substring(lastAtPos + 1, position);
            if (!query.includes(' ')) {
                setMentionSearch(query);
                setShowMentions(true);
                return;
            }
        }
        setShowMentions(false);
    };

    const shouldRetryPreviousRequest = (inputText: string): boolean => {
        const normalized = (inputText || '').replace(/\s+/g, '').toLowerCase();
        if (!normalized) return false;
        if (normalized.length > 18) return false;
        return CONTINUE_RETRY_HINTS.some((hint) => normalized.includes(hint));
    };

    const hydrateMessageFromRaw = (rawContent: string) => {
        const parsed = extractSignalsFromBuffer(rawContent || '');
        const cleanedText = stripSignalPayload(parsed.text || rawContent || '');
        let disambiguateOptions: string[] | undefined;
        let artifactActions: Message['artifactActions'];
        const progressSteps: string[] = [];

        for (const signal of parsed.signals) {
            if (signal.type === 'DISAMBIGUATE_OPTIONS') {
                disambiguateOptions = signal.options;
                continue;
            }
            if (signal.type === 'PIPELINE_PROGRESS') {
                if (progressSteps[progressSteps.length - 1] !== signal.message) {
                    progressSteps.push(signal.message);
                }
                continue;
            }
            if (signal.type === 'ARTIFACT_ACTIONS') {
                artifactActions = {
                    artifactType: signal.artifact_type,
                    htmlUrl: signal.html_url,
                    pdfUrl: signal.pdf_url,
                    approvalUrl: signal.approval_url,
                    completedSteps: signal.completed_steps,
                    totalSteps: signal.total_steps,
                    missingSteps: signal.missing_steps || [],
                    missingStepGuides: signal.missing_step_guides || [],
                    missingFieldGuides: signal.missing_field_guides || [],
                };
            }
        }

        const fallbackText = artifactActions
            ? '초안 생성이 완료되었습니다. 아래 버튼에서 결과를 확인해 주세요.'
            : '';

        return {
            content: cleanedText || fallbackText,
            disambiguateOptions,
            artifactActions,
            progressSteps: progressSteps.length > 0 ? progressSteps : undefined,
        };
    };

    const selectProjectMention = (proj: any) => {
        const lastAtPos = input.lastIndexOf('@', cursorPos - 1);
        const newValue = input.substring(0, lastAtPos) + `@${proj.name} ` + input.substring(cursorPos);
        setInput(newValue);
        setShowMentions(false);
        // We could also set a target_project_id state here
        if (inputRef.current) inputRef.current.focus();
    };

    const inferApprovalStepsFromInput = (inputText: string, missingSteps: string[]) => {
        const normalized = (inputText || '').trim().toLowerCase();
        if (!normalized) return [] as string[];
        const steps: string[] = [];
        const isGenericApprovalConfirm = GENERIC_APPROVAL_CONFIRM_HINTS.some((hint) => normalized.includes(hint));

        const hasKeyFigureSignal =
            missingSteps.includes('key_figures_approved')
            && KEY_FIGURES_AUTO_APPROVAL_HINTS.some((hint) => normalized.includes(hint))
            && (/\d/.test(normalized) || /없음|미발생|적자|흑자/.test(normalized));
        if (hasKeyFigureSignal) {
            steps.push('key_figures_approved');
        }

        const hasCertificationSignal =
            missingSteps.includes('certification_path_approved')
            && CERTIFICATION_AUTO_APPROVAL_HINTS.some((hint) => normalized.includes(hint));
        if (hasCertificationSignal) {
            steps.push('certification_path_approved');
        }

        if (missingSteps.includes('summary_confirmed') && isGenericApprovalConfirm) {
            steps.push('summary_confirmed');
        }

        if (missingSteps.includes('template_selected')) {
            const hasTemplateSignal = /템플릿|양식|형식|서식/.test(normalized);
            if (hasTemplateSignal || (isGenericApprovalConfirm && missingSteps.length === 1)) {
                steps.push('template_selected');
            }
        }

        if (steps.length === 0 && isGenericApprovalConfirm && missingSteps.length === 1) {
            steps.push(missingSteps[0]);
        }

        return Array.from(new Set(steps));
    };

    const autoCompleteApprovalStepsFromInput = async (inputText: string, targetThreadId: string) => {
        if (!projectId) return;
        const latestArtifactMsg = [...messages].reverse().find(
            (m) =>
                m.role === 'assistant'
                && (!m.thread_id || m.thread_id === targetThreadId)
                && !!m.artifactActions
                && Array.isArray(m.artifactActions?.missingSteps)
                && m.artifactActions!.missingSteps!.length > 0
        );
        if (!latestArtifactMsg?.artifactActions?.missingSteps?.length) return;

        const stepsToApprove = inferApprovalStepsFromInput(inputText, latestArtifactMsg.artifactActions.missingSteps);
        if (stepsToApprove.length === 0) return;

        const approvalParams = { threadId: targetThreadId };
        for (const step of stepsToApprove) {
            await api.post(
                `/projects/${projectId}/artifacts/business_plan/approval`,
                { step, approved: true },
                { params: approvalParams }
            );
        }
        const approvalRes = await api.get(
            `/projects/${projectId}/artifacts/business_plan/approval`,
            { params: approvalParams }
        );
        const data = approvalRes.data || {};

        setMessages((prev) =>
            prev.map((m) => {
                if (m.id !== latestArtifactMsg.id || !m.artifactActions) return m;
                return {
                    ...m,
                    artifactActions: {
                        ...m.artifactActions,
                        completedSteps: 4 - ((data.missing_steps || []).length || 0),
                        totalSteps: 4,
                        missingSteps: Array.isArray(data.missing_steps) ? data.missing_steps : [],
                        missingStepGuides: Array.isArray(data.missing_step_guides) ? data.missing_step_guides : [],
                    },
                };
            })
        );

        setMessages((prev) => [
            ...prev,
            {
                id: `${Date.now()}-approval-auto`,
                role: 'assistant',
                content: `입력 내용을 기반으로 승인 단계를 자동 반영했습니다: ${stepsToApprove
                    .map((step) => APPROVAL_STEP_LABELS[step] || step)
                    .join(', ')}`,
                thread_id: targetThreadId,
            },
        ]);
    };

    const handleSend = async (type: 'chat' | 'job' = 'chat', messageText?: string) => {
        const sendText = (type === 'chat' ? (messageText ?? input) : '🚀 START TASK');
        if (!sendText.trim() && type === 'chat') return;
        if (loading) return;
        if (!effectiveProjectId) {
            setContextGuardMessage('상담방을 준비 중입니다. 잠시 후 다시 시도해 주세요.');
            return;
        }
        const requestedThreadId = resolvedThreadId || currentThreadId;
        const nextThreadId = await resolveAndSetThread(effectiveProjectId, requestedThreadId);
        if (!nextThreadId) {
            setLoading(false);
            setContextGuardMessage('상담 스레드를 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.');
            return;
        }

        if (!threadId) {
            router.replace(`/chat?projectId=${effectiveProjectId}&threadId=${nextThreadId}`);
        }
        if (!currentThreadId) {
            setCurrentThreadId(nextThreadId);
            setStoreCurrentThreadId(nextThreadId);
        }

        let requestText = sendText;
        let retriedFromPending = false;
        if (type === 'chat' && shouldRetryPreviousRequest(sendText)) {
            const pending = pendingRetryRef.current;
            if (pending?.requestText && (!pending.threadId || pending.threadId === nextThreadId)) {
                requestText = pending.requestText;
                retriedFromPending = true;
            }
        }

        // Clear input for chat type
        if (type === 'chat') {
            setInput('');
            setReadyToStart(false); // Reset gate on new chat
            setTaskStarted(false);  // Reset task state
        }

        const userMsg: Message = {
            id: Date.now().toString(),
            role: 'user',
            content: sendText,
            thread_id: nextThreadId,
        };

        setMessages(prev => [...prev, userMsg]);
        if (type === 'chat') {
            try {
                await autoCompleteApprovalStepsFromInput(sendText, nextThreadId);
            } catch (autoApprovalError) {
                console.warn('[ChatInterface] auto approval step update skipped', autoApprovalError);
            }
        }
        setLoading(true);
        let activeAiMsgId: string | null = null;
        let partialAssistantContent = '';

        try {
            if (type === 'job') {
                // Show logs window immediately
                setTaskStarted(true);
                setShowLogs(true);
                setLogs(['Initializing workflow execution...', 'Authenticating context...']);
                setReadyToStart(false);

                try {
                    const response = await api.post(`/projects/${effectiveProjectId}/execute`);

	                    const aiMsg: Message = {
	                        id: (Date.now() + 1).toString(),
	                        role: 'assistant',
	                        content: `🚀 Workflow started for project **${activeProject?.name || effectiveProjectId}**.\nExecution ID: \`${response.data.execution_id}\`\nMonitoring real-time logs in the console.`,
	                        hasLogs: true,
	                        thread_id: nextThreadId,
	                    };
                    setMessages(prev => [...prev, aiMsg]);
                    setLogs(prev => [...prev, `Workflow accepted by engine. Execution ID: ${response.data.execution_id}`, `Streaming real-time logs...`]);
                } catch (execError: any) {
                    console.error("Execution trigger failed", execError);
                    setLogs(prev => [...prev, `❌ FAILED: ${execError.response?.data?.detail || execError.message}`]);
	                    setMessages(prev => [...prev, {
	                        id: Date.now().toString(),
	                        role: 'assistant',
	                        content: `⚠️ 작업 시작 중 오류가 발생했습니다: ${execError.response?.data?.detail || execError.message}`,
	                        thread_id: nextThreadId,
	                    }]);
                }
            } else {
                // [TODO 8] Streaming Chat with Tool Call Interceptor (2nd Layer Protection)
                const token = useAuthStore.getState().token;
                if (!token) {
                    console.error("Auth token is missing!");
                    throw new Error("Authentication token is missing. Please log in again.");
                }
                const authHeader = `Bearer ${token}`;

                // Use absolute URL from axios config or fallback
                const currentHostname = (window.location.hostname === 'localhost' || window.location.hostname === '0.0.0.0')
                    ? '127.0.0.1'
                    : window.location.hostname;
                const baseURL = api.defaults.baseURL || `http://${currentHostname}:8002/api/v1`;

                let threadHistory: { role: string; content: string }[] = buildThreadPayloadFromMessages(nextThreadId);
                if (threadHistory.length === 0) {
                    try {
                        const historyRes = await api.get(`/projects/${effectiveProjectId}/threads/${nextThreadId}/messages`, {
                            params: { limit },
                        });
                        threadHistory = Array.isArray(historyRes.data)
                            ? historyRes.data.map((msg: any) => ({
                                role: msg.role,
                                content: stripSignalPayload(msg.content || ''),
                            }))
                            : [];
                    } catch (error: any) {
                        console.warn('[ChatInterface] Failed to rebuild thread history for stream payload', error);
                    }
                }

	                const response = await fetch(`${baseURL}/master/chat-stream`, {
	                    method: 'POST',
	                    headers: {
	                        'Content-Type': 'application/json',
	                        'Authorization': authHeader
	                    },
	                    body: JSON.stringify({
	                        message: requestText,
	                        history: [
	                            ...threadHistory,
	                            { role: 'user', content: requestText },
	                        ],
	                        project_id: effectiveProjectId,
	                        thread_id: nextThreadId,
	                        mode,
	                        mode_change_origin: modeChangeOrigin,
	                    })
	                });
	                setModeChangeOrigin('auto');

                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

                // [v4.2] Extract Request ID for Admin Debugging
                const requestId = response.headers.get('X-Request-Id') || undefined;
                
                // [v5.0 DEBUG] Log request_id extraction
                console.log(`[v5.0 handleSend] New message request_id: ${requestId || 'MISSING'}`);
                if (!requestId) {
                    console.warn('[v5.0 handleSend] ⚠️ X-Request-Id header not found! Check backend CORS settings.');
                }

                const reader = response.body?.getReader();
                const decoder = new TextDecoder();

                // [Fix] 스트리밍 시작 시 로딩 상태 해제 (중복 아이콘 방지)
                setLoading(false);

	                let accumulatedContent = '';
	                let signalBuffer = '';
	                let hasParsedDisambiguateSignal = false;
	                let hasParsedArtifactSignal = false;
	                let sawReadyToStart = false;
	                const aiMsgId = (Date.now() + 1).toString();
	                activeAiMsgId = aiMsgId;

	                const aiMsg: Message = {
	                    id: aiMsgId,
	                    role: 'assistant',
	                    content: '',
	                    hasLogs: false,
	                    isStreaming: true,
	                    thread_id: nextThreadId,
	                    request_id: requestId,
	                };
	                setMessages(prev => [...prev, aiMsg]);

	                if (reader) {
	                    while (true) {
	                        const { done, value } = await reader.read();
	                        if (done) break;

	                        const chunk = decoder.decode(value, { stream: true });
	                        const filteredChunk = chunk.replace(/<[|｜]tool[▁_]calls[▁_]begin[|｜]>|<[|｜]tool[▁_]calls[▁_]end[|｜]>|<[|｜]tool[▁_]result[▁_]begin[|｜]>|<[|｜]tool[▁_]result[▁_]end[|｜]>/g, '');

	                        signalBuffer += filteredChunk;
	                        const { text, carry, signals } = extractSignalsFromBuffer(signalBuffer);
	                        signalBuffer = carry;

	                        signals.forEach((signal) => {
	                            if (signal.type === 'PIPELINE_PROGRESS') {
	                                setMessages((prev) =>
	                                    prev.map((m) => {
	                                        if (m.id !== aiMsgId) return m;
	                                        const prevSteps = Array.isArray(m.progressSteps) ? m.progressSteps : [];
	                                        if (prevSteps[prevSteps.length - 1] === signal.message) {
	                                            return m;
	                                        }
	                                        return {
	                                            ...m,
	                                            progressSteps: [...prevSteps, signal.message],
	                                        };
	                                    })
	                                );
	                                return;
	                            }

	                            if (signal.type === 'MODE_SWITCH') {
	                                setMode(signal.mode);
	                                setModeChangeOrigin('auto');
	                                return;
	                            }

	                            if (signal.type === 'DISAMBIGUATE_OPTIONS' && !hasParsedDisambiguateSignal) {
	                                hasParsedDisambiguateSignal = true;
	                                setMessages((prev) =>
	                                    prev.map((m) =>
	                                        m.id === aiMsgId
	                                            ? { ...m, disambiguateOptions: signal.options }
	                                            : m
	                                    )
	                                );
	                                return;
	                            }

	                            if (signal.type === 'ARTIFACT_ACTIONS' && !hasParsedArtifactSignal) {
	                                hasParsedArtifactSignal = true;
	                                setMessages((prev) =>
	                                    prev.map((m) =>
	                                        m.id === aiMsgId
	                                            ? {
	                                                ...m,
	                                                artifactActions: {
	                                                    artifactType: signal.artifact_type,
	                                                    htmlUrl: signal.html_url,
	                                                    pdfUrl: signal.pdf_url,
	                                                    approvalUrl: signal.approval_url,
	                                                    completedSteps: signal.completed_steps,
	                                                    totalSteps: signal.total_steps,
	                                                    missingSteps: signal.missing_steps || [],
	                                                    missingStepGuides: signal.missing_step_guides || [],
	                                                    missingFieldGuides: signal.missing_field_guides || [],
	                                                },
	                                            }
	                                            : m
	                                    )
	                                );
	                                return;
	                            }

	                            if (signal.type === 'READY_TO_START') {
	                                sawReadyToStart = true;
	                                setReadyToStart(true);
	                                setFinalSummary(signal.final_summary);
	                            }
	                        });

	                        if (text) {
	                            accumulatedContent += text;
	                        }

	                        partialAssistantContent = stripSignalPayload(accumulatedContent);
	                        setMessages(prev => prev.map(m =>
	                            m.id === aiMsgId
	                                ? { ...m, content: partialAssistantContent, isStreaming: true }
	                                : m
	                        ));
	                    }
	                }

	                if (signalBuffer.trim()) {
	                    accumulatedContent += signalBuffer;
	                }
	                partialAssistantContent = stripSignalPayload(accumulatedContent);
	                const finalizedContent = partialAssistantContent || (
	                    sawReadyToStart
	                        ? "설정이 완료되었습니다. 아래 [START TASK] 버튼을 눌러 작업을 시작해 주세요."
	                        : ''
	                );
                    pendingRetryRef.current = null;
	                setMessages(prev => prev.map(m =>
	                    m.id === aiMsgId
	                        ? { ...m, content: finalizedContent, isStreaming: false }
	                        : m
	                ));
                    if (retriedFromPending) {
                        setMessages((prev) => [
                            ...prev,
                            {
                                id: `${Date.now()}-retry-info`,
                                role: 'assistant',
                                content: '방금 끊긴 요청을 이어서 재시도해 완료했습니다.',
                                thread_id: nextThreadId,
                            },
                        ]);
                    }
	            }
        } catch (error: any) {
            console.error('Failed to send message', error);
            const rawErrorMessage = error?.response?.data?.detail || error?.message || 'unknown error';
            const friendlyMessage = String(rawErrorMessage).toLowerCase().includes('network error')
                ? '연결이 중간에 끊어졌습니다. 같은 내용을 한 번 더 보내면 검증/생성을 이어서 진행할 수 있습니다.'
                : `오류가 발생했습니다: ${rawErrorMessage}`;
            if (type === 'chat' && String(rawErrorMessage).toLowerCase().includes('network error') && requestText?.trim()) {
                pendingRetryRef.current = { threadId: nextThreadId, requestText };
            }

            if (activeAiMsgId) {
                setMessages((prev) =>
                    prev.map((m) =>
                        m.id === activeAiMsgId
                            ? {
                                ...m,
                                isStreaming: false,
                                content: `${stripSignalPayload(partialAssistantContent || m.content || '')}\n\n${friendlyMessage}`.trim(),
                            }
                            : m
                    )
                );
            } else {
                setMessages(prev => [...prev, {
                    id: Date.now().toString(),
                    role: 'assistant',
                    content: friendlyMessage,
                    thread_id: nextThreadId,
                }]);
            }
        } finally {
            setLoading(false);
        }
    };

    const handleTabChange = (tab: string, reqId: string, nodeId?: string) => {
        // [v5.0] URL 파라미터 확장: nodeId 추가로 탭에서 자동 노드 선택
        const params = new URLSearchParams();
        params.set('tab', tab);
        params.set('request_id', reqId);
        if (nodeId) params.set('nodeId', nodeId);
        if (projectId) params.set('projectId', projectId);
        
        router.push(`?${params.toString()}`, { scroll: false });
    };

    const filteredProjects = projects.filter(p =>
        p.name.toLowerCase().includes(mentionSearch.toLowerCase())
    );

    const visibleMessages = visibleThreadId
        ? messages.filter((msg) => msg.thread_id === visibleThreadId)
        : messages;

    const normalizeApiPath = (pathOrUrl?: string) => {
        if (!pathOrUrl) return '';
        if (pathOrUrl.startsWith('/api/v1/')) {
            return pathOrUrl.replace('/api/v1', '');
        }
        if (pathOrUrl.startsWith('/')) return pathOrUrl;
        if (/^https?:\/\//i.test(pathOrUrl)) {
            try {
                const parsed = new URL(pathOrUrl);
                const fullPath = `${parsed.pathname}${parsed.search || ''}`;
                return fullPath.startsWith('/api/v1/')
                    ? fullPath.replace('/api/v1', '')
                    : fullPath;
            } catch {
                return '';
            }
        }
        return pathOrUrl;
    };

    const openArtifactUrl = async (pathOrUrl?: string) => {
        const apiPath = normalizeApiPath(pathOrUrl);
        if (!apiPath) return;
        const approvalParams = visibleThreadId ? { threadId: visibleThreadId } : undefined;

        try {
            if (apiPath.includes('/approval')) {
                const res = await api.get(apiPath, { params: approvalParams });
                const data = res.data || {};
                const missing = Array.isArray(data.missing_steps) ? data.missing_steps : [];
                const missingGuides = Array.isArray(data.missing_step_guides) ? data.missing_step_guides : [];
                const completed = 4 - missing.length;
                const guideText = missing.length > 0
                    ? missing.map((step: string, idx: number) => {
                        const guideFromBackend = missingGuides[idx];
                        return `- ${guideFromBackend || `${APPROVAL_STEP_LABELS[step] || step}: ${APPROVAL_STEP_GUIDES[step] || '검토 후 완료해 주세요.'}`}`;
                    }).join('\n')
                    : '모든 승인 단계가 완료되었습니다. PDF 다운로드가 가능합니다.';
                setMessages((prev) => [
                    ...prev,
                    {
                        id: `${Date.now()}-approval-view`,
                        role: 'assistant',
                        content: `현재 승인 진행은 ${completed}/4 입니다.\n${guideText}`,
                        thread_id: visibleThreadId || undefined,
                    },
                ]);
                return;
            }

            const res = await api.get(apiPath, { responseType: 'blob', params: approvalParams });
            const contentType = res.headers?.['content-type'] || 'application/octet-stream';
            const isMarkdownLike =
                apiPath.includes('format=markdown')
                || contentType.includes('text/markdown')
                || contentType.includes('text/plain');

            if (isMarkdownLike) {
                const rawText = await new Blob([res.data], { type: contentType }).text();
                const markdownText = rawText.includes('\\n') && !rawText.includes('\n')
                    ? rawText.replace(/\\n/g, '\n')
                    : rawText;
                setMessages((prev) => [
                    ...prev,
                    {
                        id: `${Date.now()}-artifact-markdown`,
                        role: 'assistant',
                        content: markdownText,
                        thread_id: visibleThreadId || undefined,
                    },
                ]);
                return;
            }
            const blob = new Blob([res.data], { type: contentType });
            const blobUrl = URL.createObjectURL(blob);
            const win = window.open(blobUrl, '_blank', 'noopener,noreferrer');

            if (!win) {
                const a = document.createElement('a');
                a.href = blobUrl;
                a.download = apiPath.includes('format=pdf') ? 'business_plan.pdf' : 'business_plan.html';
                document.body.appendChild(a);
                a.click();
                a.remove();
            }
            window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
        } catch (e: any) {
            const isPdfDownload = apiPath.includes('format=pdf');
            const isApprovalBlocked = e?.response?.status === 409 && isPdfDownload;
            const errorMessage = isApprovalBlocked
                ? 'PDF 다운로드를 위해 남은 단계를 진행해주세요.'
                : `파일 열기/다운로드 중 오류가 발생했습니다: ${e?.response?.data?.detail?.message || e?.message || 'unknown error'}`;
            setMessages((prev) => [
                ...prev,
                {
                    id: `${Date.now()}-artifact-open-error`,
                    role: 'assistant',
                    content: errorMessage,
                    thread_id: visibleThreadId || undefined,
                },
            ]);
        }
    };

    const completeApprovalStep = async (messageId: string, step: string) => {
        if (!projectId) return;
        const approvalParams = visibleThreadId ? { threadId: visibleThreadId } : undefined;
        try {
            await api.post(`/projects/${projectId}/artifacts/business_plan/approval`, {
                step,
                approved: true,
            }, { params: approvalParams });
            const approvalRes = await api.get(`/projects/${projectId}/artifacts/business_plan/approval`, { params: approvalParams });
            const data = approvalRes.data || {};
            setMessages((prev) =>
                prev.map((m) => {
                    if (m.id !== messageId || !m.artifactActions) return m;
                    return {
                        ...m,
                        artifactActions: {
                            ...m.artifactActions,
                            completedSteps: 4 - ((data.missing_steps || []).length || 0),
                            totalSteps: 4,
                            missingSteps: Array.isArray(data.missing_steps) ? data.missing_steps : [],
                            missingStepGuides: Array.isArray(data.missing_step_guides) ? data.missing_step_guides : [],
                        },
                    };
                })
            );
        } catch (e: any) {
            setMessages((prev) => [
                ...prev,
                {
                    id: `${Date.now()}-approval-error`,
                    role: 'assistant',
                    content: `승인 단계 업데이트 중 오류가 발생했습니다: ${e?.response?.data?.detail?.message || e?.message || 'unknown error'}`,
                    thread_id: visibleThreadId || undefined,
                },
            ]);
        }
    };

    return (
        <div className="flex flex-col h-dvh relative bg-zinc-950 text-white overflow-hidden">
            {/* Header / Project Badge */}
            <div className="px-4 py-2 border-b border-zinc-800 bg-zinc-900/30 flex items-center justify-between">
                {/* [v5.0] Swipe Navigation Container */}
                <div 
                    className="flex items-center gap-2 flex-1 overflow-hidden"
                    onTouchStart={(e) => {
                        const touchStart = e.targetTouches[0].clientX;
                        e.currentTarget.setAttribute('data-touch-start', touchStart.toString());
                    }}
                    onTouchEnd={(e) => {
                        const touchStart = parseFloat(e.currentTarget.getAttribute('data-touch-start') || '0');
                        const touchEnd = e.changedTouches[0].clientX;
                        const diff = touchStart - touchEnd;
                        
                        if (Math.abs(diff) > 50) { // Threshold 50px
                            const currentIndex = projects.findIndex(p => p.id === projectId);
                            if (currentIndex === -1) return;
                            
                            if (diff > 0) { // Swipe Left -> Next Project
                                const nextProject = projects[currentIndex + 1];
                                if (nextProject) router.push(`?projectId=${nextProject.id}`);
                            } else { // Swipe Right -> Prev Project
                                const prevProject = projects[currentIndex - 1];
                                if (prevProject) router.push(`?projectId=${prevProject.id}`);
                            }
                        }
                    }}
                >
                    <div className={`w-2 h-2 rounded-full ${projectId ? 'bg-indigo-500 animate-pulse' : 'bg-zinc-600'}`}></div>
                    <span className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2 select-none cursor-ew-resize">
                        {activeProject ? (
                            <>
                                <span className="text-indigo-400">{activeProject.name}</span>
                                <span className="text-zinc-600">/</span>
                                <span>Chat Room</span>
                            </>
                        ) : contextGuardMessage || 'Global System Context'}
                    </span>
                    {threadId && (
                        <span className="text-[10px] bg-zinc-800 px-2 py-0.5 rounded text-zinc-500 font-mono ml-2">
                            #{threadId.slice(-6)}
                        </span>
                    )}
                </div>
                {hasMore && (
                    <button
                        onClick={handleLoadMore}
                        className="text-[10px] text-zinc-500 hover:text-indigo-400 transition-colors"
                    >
                        Load Previous Messages
                    </button>
                )}
            </div>

            {/* Chat Area */}
            <div className="flex-1 overflow-y-auto p-4 space-y-6 min-h-0 scrollbar-thin scrollbar-thumb-zinc-800 scrollbar-track-transparent">
                {contextGuardMessage && (
                    <div className="rounded-xl border border-yellow-400/40 bg-yellow-400/10 text-yellow-200 px-4 py-3 text-sm">
                        {contextGuardMessage}
                    </div>
                )}
                {visibleMessages.length === 0 && !loading ? (
                    <div className="flex flex-col items-center justify-center h-full text-center space-y-6 opacity-30">
                        <Bot size={64} className="text-indigo-500" />
                        <div>
                            <h2 className="text-xl font-bold text-zinc-200">AIBizPlan 시작 안내</h2>
                            <p className="text-sm text-zinc-500 mt-2 text-left whitespace-pre-wrap max-w-2xl">
                                {projectId ? DEFAULT_WELCOME_MESSAGES.room_ready : DEFAULT_WELCOME_MESSAGES.first_login}
                            </p>
                        </div>
                    </div>
                ) : (
                    visibleMessages.map((msg) => (
                        <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                            <div className={`flex gap-3 max-w-[85%] ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
                                <div className={`w-8 h-8 rounded-lg flex-shrink-0 flex items-center justify-center ${msg.role === 'user' ? 'bg-indigo-600' : 'bg-zinc-800 border border-zinc-700'
                                    }`}>
                                    {msg.role === 'user' ? <UserIcon size={16} /> : <Bot size={16} className="text-indigo-400" />}
                                </div>
                                <div className={`rounded-2xl px-4 py-2.5 ${msg.role === 'user'
                                    ? 'bg-indigo-600/10 text-zinc-100 border border-indigo-500/30'
                                    : 'bg-zinc-900 text-zinc-300 border border-zinc-800 shadow-sm'
                                    }`}>
                                    <div className="whitespace-pre-wrap text-sm leading-relaxed break-words">
                                        {msg.role === 'assistant'
                                            ? renderMarkdownMessageContent(msg.content)
                                            : msg.content
                                        }
                                    </div>

                                    {msg.role === 'assistant' && msg.progressSteps && msg.progressSteps.length > 0 && (
                                        <div className="mt-3 rounded-lg border border-zinc-700/60 bg-zinc-800/30 px-3 py-2">
                                            <div className="text-[11px] uppercase tracking-wide text-zinc-400 mb-1 flex items-center gap-1.5">
                                                <span>진행 상태</span>
                                                {msg.isStreaming && <Loader2 size={11} className="animate-spin text-indigo-300" />}
                                            </div>
                                            <div className="space-y-1">
                                                {msg.progressSteps.map((step, idx) => (
                                                    <div key={`${msg.id}-progress-${idx}`} className="text-xs text-zinc-200">
                                                        {idx + 1}. {step}
                                                    </div>
                                                ))}
                                            </div>
                                            <div className="mt-2">
                                                <div className="h-1.5 rounded-full bg-zinc-700/70 overflow-hidden">
                                                    <div
                                                        className={`h-full bg-indigo-400 transition-all duration-500 ${msg.isStreaming ? 'animate-pulse' : ''}`}
                                                        style={{ width: `${getProgressPercent(msg.progressSteps)}%` }}
                                                    />
                                                </div>
                                                <div className="mt-1 text-[11px] text-zinc-400">
                                                    {msg.isStreaming
                                                        ? `${msg.progressSteps[msg.progressSteps.length - 1]}...`
                                                        : '처리 완료'}
                                                </div>
                                            </div>
                                        </div>
                                    )}

                                    {msg.role === 'assistant' && msg.isStreaming && (!msg.progressSteps || msg.progressSteps.length === 0) && (
                                        <div className="mt-3 inline-flex items-center gap-2 rounded-lg border border-zinc-700/60 bg-zinc-800/30 px-3 py-1.5 text-xs text-zinc-300">
                                            <Loader2 size={12} className="animate-spin text-indigo-300" />
                                            <span>응답 생성 중...</span>
                                        </div>
                                    )}

                                    {msg.role === 'assistant' && msg.disambiguateOptions && msg.disambiguateOptions.length > 0 && (
                                        <div className="mt-3 flex flex-wrap gap-2">
                                            {msg.disambiguateOptions.map((option) => (
                                                <button
                                                    key={option}
                                                    onClick={() => sendDisambiguateOption(option)}
                                                    className="text-xs px-3 py-2 rounded-lg bg-indigo-600/20 border border-indigo-500/40 text-indigo-200 hover:bg-indigo-600/30 transition-colors"
                                                >
                                                    {option}
                                                </button>
                                            ))}
                                        </div>
                                    )}

                                    {msg.role === 'assistant' && msg.artifactActions && (
                                        <div className="mt-3 space-y-2 border-t border-zinc-700/60 pt-3">
                                            <div className="flex flex-wrap gap-2">
                                                {msg.artifactActions.htmlUrl && (
                                                    <button
                                                        onClick={() => openArtifactUrl(msg.artifactActions?.htmlUrl)}
                                                        className="text-xs px-3 py-2 rounded-lg bg-indigo-600/20 border border-indigo-500/40 text-indigo-200 hover:bg-indigo-600/30 transition-colors"
                                                    >
                                                        결과 보기
                                                    </button>
                                                )}
                                                {msg.artifactActions.pdfUrl && (
                                                    <button
                                                        onClick={() => openArtifactUrl(msg.artifactActions?.pdfUrl)}
                                                        className="text-xs px-3 py-2 rounded-lg bg-zinc-700/40 border border-zinc-500/40 text-zinc-200 hover:bg-zinc-700/60 transition-colors"
                                                    >
                                                        PDF 다운로드
                                                    </button>
                                                )}
                                                {msg.artifactActions.approvalUrl && (
                                                    <button
                                                        onClick={() => openArtifactUrl(msg.artifactActions?.approvalUrl)}
                                                        className="text-xs px-3 py-2 rounded-lg bg-emerald-600/20 border border-emerald-500/40 text-emerald-200 hover:bg-emerald-600/30 transition-colors"
                                                    >
                                                        승인 상태 확인
                                                    </button>
                                                )}
                                            </div>
                                            {typeof msg.artifactActions.completedSteps === 'number' && typeof msg.artifactActions.totalSteps === 'number' && (
                                                <div className="text-xs text-zinc-400">
                                                    PDF 승인 진행률: {msg.artifactActions.completedSteps}/{msg.artifactActions.totalSteps}
                                                </div>
                                            )}
                                            {msg.artifactActions.missingSteps && msg.artifactActions.missingSteps.length > 0 && (
                                                <div className="space-y-1">
                                                    <div className="text-xs text-amber-300">남은 단계 안내</div>
                                                    {msg.artifactActions.missingSteps.map((step, idx) => {
                                                        const guideFromBackend = msg.artifactActions?.missingStepGuides?.[idx];
                                                        const guideText = guideFromBackend || `${APPROVAL_STEP_LABELS[step] || step}: ${APPROVAL_STEP_GUIDES[step] || '검토 후 완료해 주세요.'}`;
                                                        return (
                                                        <div key={`${msg.id}-${step}-${idx}`} className="text-xs text-zinc-300 flex items-center justify-between gap-2">
                                                            <span>
                                                                - {guideText}
                                                            </span>
                                                            <button
                                                                onClick={() => completeApprovalStep(msg.id, step)}
                                                                className="shrink-0 px-2 py-1 rounded bg-amber-500/20 border border-amber-400/40 text-amber-200 hover:bg-amber-500/30"
                                                            >
                                                                완료 처리
                                                            </button>
                                                        </div>
                                                    )})}
                                                </div>
                                            )}
                                            {msg.artifactActions.missingFieldGuides && msg.artifactActions.missingFieldGuides.length > 0 && (
                                                <div className="space-y-1">
                                                    <div className="text-xs text-sky-300">양식 입력 보강 안내</div>
                                                    <div className="text-[11px] text-zinc-500">
                                                        참고: 이 항목은 문서 품질 보강용이며, PDF 승인 진행률(4단계)과는 별개입니다.
                                                    </div>
                                                    {msg.artifactActions.missingFieldGuides.map((guide, idx) => (
                                                        <div key={`${msg.id}-field-guide-${idx}`} className="text-xs text-zinc-300">
                                                            - {guide}
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    {/* [v4.2 UX] Admin-Only Source Bar (Enhanced) */}
                                    {/* Removed !loading check to show bar immediately if request_id is present */}
                                    {msg.role === 'assistant' && user?.role === 'super_admin' && msg.request_id && (() => {
                                        // [v5.0 DEBUG] Log each message's request_id when rendering
                                        console.log(`[v5.0 MessageRender] Message ID: ${msg.id}, request_id: ${msg.request_id?.substring(0, 8)}..., content preview: ${msg.content.substring(0, 30)}...`);
                                        return (
                                            <MessageAuditBar
                                                requestId={msg.request_id}
                                                projectId={projectId}
                                                onTabChange={handleTabChange}
                                            />
                                        );
                                    })()}

                                    {msg.hasLogs && (
                                        <button
                                            onClick={() => setShowLogs(true)}
                                            className="mt-3 flex items-center gap-1.5 text-[10px] text-indigo-400 hover:text-indigo-300 font-bold uppercase bg-indigo-500/10 px-2 py-1 rounded transition-all"
                                        >
                                            <Zap size={10} />
                                            Live Execution Logs
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    ))
                )}
                {loading && (
                    <div className="flex justify-start gap-3">
                        <div className="w-8 h-8 rounded-lg bg-zinc-800 border border-zinc-700 flex items-center justify-center">
                            <Bot size={16} className="text-indigo-400" />
                        </div>
                        <div className="bg-zinc-900 rounded-2xl px-4 py-3 border border-zinc-800">
                            <Loader2 size={16} className="animate-spin text-zinc-500" />
                        </div>
                    </div>
                )}
                <div ref={messagesEndRef} />
            </div>

            {/* Mention UI */}
            {showMentions && (
                <div className="absolute bottom-24 left-4 w-64 bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl overflow-hidden z-50 animate-in slide-in-from-bottom-2">
                    <div className="p-2 border-b border-zinc-800 bg-zinc-950/50 flex items-center gap-2">
                        <AtSign size={14} className="text-indigo-500" />
                        <span className="text-[10px] font-bold text-zinc-400 uppercase">Mention Project</span>
                    </div>
                    <div className="max-h-48 overflow-y-auto">
                        {filteredProjects.length === 0 ? (
                            <div className="p-4 text-xs text-zinc-600 italic">No matches found</div>
                        ) : (
                            filteredProjects.map(proj => (
                                <button
                                    key={proj.id}
                                    onClick={() => selectProjectMention(proj)}
                                    className="w-full flex items-center gap-3 px-3 py-2 hover:bg-zinc-800 transition-colors text-left"
                                >
                                    <div className="w-2 h-2 rounded-full bg-indigo-500"></div>
                                    <div className="flex flex-col">
                                        <span className="text-sm text-zinc-200 font-medium">{proj.name}</span>
                                        <span className="text-[10px] text-zinc-500 font-mono">{proj.id}</span>
                                    </div>
                                </button>
                            ))
                        )}
                    </div>
                </div>
            )}

            {/* Input Area */}
            <div className="p-4 border-t border-zinc-800/50 bg-zinc-950">
                <div className="max-w-4xl mx-auto">
                    {(selectedFiles.length > 0 || uploadNotice) && (
                        <div className="mb-3 rounded-xl border border-zinc-800 bg-zinc-900/40 px-3 py-2 space-y-2">
                            {selectedFiles.length > 0 && (
                                <div className="space-y-2">
                                    <div className="text-[11px] text-zinc-400 uppercase tracking-wide">
                                        첨부파일 미리보기 ({selectedFiles.length}개)
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                        {selectedFiles.map((file) => (
                                            <span
                                                key={`${file.name}-${file.size}-${file.lastModified}`}
                                                className="inline-flex items-center gap-2 rounded-lg bg-zinc-800/80 px-2 py-1 text-xs text-zinc-200 border border-zinc-700"
                                            >
                                                <FileText size={12} />
                                                <span className="font-medium">{file.name}</span>
                                                <span className="text-zinc-500">({formatFileSize(file.size)})</span>
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {!selectedFiles.length && uploadNotice ? (
                                <div className={`text-xs ${uploadNotice.type === 'success' ? 'text-emerald-400' : uploadNotice.type === 'partial' ? 'text-amber-300' : 'text-rose-400'}`}>
                                    {uploadNotice.text}
                                </div>
                            ) : (
                                isUploading && (
                                    <div className="text-xs text-indigo-300 animate-pulse flex items-center gap-2">
                                        <Loader2 size={12} className="animate-spin" />
                                        첨부파일 업로드 진행 중
                                    </div>
                                )
                            )}
                        </div>
                    )}

                    <div className={`relative flex items-end gap-2 rounded-2xl border bg-zinc-900/30 p-2 shadow-inner transition-all ${MODE_CONFIG[mode].border} focus-within:ring-1 focus-within:ring-opacity-20`}>
                        {/* [v4.0] Mode Switcher */}
                        <div className="relative">
                            <button
                                onClick={() => setShowModeMenu(!showModeMenu)}
                                className={`p-2.5 rounded-xl transition-colors flex-shrink-0 ${MODE_CONFIG[mode].text} hover:bg-zinc-800`}
                                title={`Current Mode: ${MODE_CONFIG[mode].label}`}
                            >
                                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${MODE_CONFIG[mode].border}`}>
                                    <div className={`w-2.5 h-2.5 rounded-full ${MODE_CONFIG[mode].bg}`}></div>
                                </div>
                            </button>
                            {showModeMenu && (
                                <div className="absolute bottom-12 left-0 w-48 bg-zinc-900 border border-zinc-800 rounded-xl shadow-xl overflow-hidden z-50 animate-in slide-in-from-bottom-2">
                                    <div className="p-2 text-[10px] font-bold text-zinc-500 uppercase bg-zinc-950/50 border-b border-zinc-800">
                                        Select Conversation Mode
                                    </div>
                                    {Object.entries(MODE_CONFIG).map(([key, config]) => (
                                        <button
                                            key={key}
                                            onClick={() => {
                                                const nextMode = key as ConversationMode;
                                                setMode(nextMode);
                                                setModeChangeOrigin('user');
                                                pushModeGuideMessage(nextMode);
                                                setShowModeMenu(false);
                                            }}
                                            className={`w-full text-left px-4 py-3 text-sm hover:bg-zinc-800 transition-colors flex items-center gap-3 ${mode === key ? 'bg-zinc-800/50' : ''}`}
                                        >
                                            <div className={`w-3 h-3 rounded-full ${config.bg}`}></div>
                                            <span className={mode === key ? 'text-white font-medium' : 'text-zinc-400'}>
                                                {config.label}
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>

                        <input
                            type="file"
                            ref={fileInputRef}
                            onChange={handleFileUpload}
                            multiple
                            className="hidden"
                        />
                        <button 
                            onClick={() => fileInputRef.current?.click()}
                            disabled={isUploading}
                            className={`p-2.5 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 rounded-xl transition-colors flex-shrink-0 ${isUploading ? 'animate-pulse opacity-50' : ''}`}
                        >
                            <Paperclip size={20} />
                        </button>
                        
                        <textarea
                            ref={inputRef}
                            value={input}
                            onChange={handleInput}
                            onKeyDown={(e) => {
                                // [Mobile Guard] Prevent Enter submission on mobile devices
                                if (e.nativeEvent.isComposing) return; // [Fix] IME 중복 전송 방지

                                const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent) || window.innerWidth < 768;

                                if (e.key === 'Enter' && !e.shiftKey) {
                                    if (isMobile) return;
                                    e.preventDefault();
                                    handleSend('chat');
                                }
                            }}
                            placeholder="텍스트를 입력해주세요."
                            className="flex-1 max-h-40 min-h-[2.75rem] bg-transparent py-3 px-1 text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none resize-none scrollbar-hide"
                            rows={1}
                        />

                        <div className="flex items-center gap-2 p-1">
                            <button
                                onClick={() => handleSend('chat')}
                                disabled={!input.trim() || loading || !effectiveProjectId}
                                className="p-2 text-zinc-500 hover:text-white hover:bg-zinc-800 rounded-xl transition-colors disabled:opacity-30"
                            >
                                <Send size={20} />
                            </button>
                        </div>
                    </div>

                    {/* Action Area for Dynamic Buttons */}
                    {(readyToStart || taskStarted) && (
                        <div className="mt-4 p-4 bg-indigo-600/10 border border-indigo-500/30 rounded-2xl flex items-center justify-between animate-in fade-in slide-in-from-bottom-2 duration-500">
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-indigo-600 rounded-lg">
                                    <Zap size={18} className="text-white" />
                                </div>
                                <div className="flex flex-col">
                                    <span className="text-xs font-bold text-indigo-400 uppercase tracking-wider">
                                        {taskStarted ? "Task in Progress" : "Ready to Execute"}
                                    </span>
                                    <p className="text-sm text-zinc-300 line-clamp-1">
                                        {taskStarted ? "Execution logs are being generated." : (finalSummary || "Plan completed. Ready to start task.")}
                                    </p>
                                </div>
                            </div>
                            {taskStarted ? (
                                <button
                                    onClick={() => setShowLogs(true)}
                                    className="flex items-center gap-2 px-6 py-2.5 bg-zinc-800 text-white rounded-xl hover:bg-zinc-700 transition-all font-bold text-sm border border-zinc-700 shadow-lg"
                                >
                                    <FileText size={16} />
                                    <span>VIEW LOGS</span>
                                </button>
                            ) : (
                                <button
                                    onClick={() => handleSend('job')}
                                    disabled={loading}
                                    className="flex items-center gap-2 px-6 py-2.5 bg-indigo-600 text-white rounded-xl hover:bg-indigo-500 transition-all font-bold text-sm shadow-lg shadow-indigo-900/40"
                                >
                                    <Zap size={16} />
                                    <span>START TASK</span>
                                </button>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Log Console */}
            <LogConsole isOpen={showLogs} onClose={() => setShowLogs(false)} logs={logs} />
        </div>
    );
}
