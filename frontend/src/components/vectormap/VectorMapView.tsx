'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useSearchParams } from 'next/navigation'; // [v5.0] For URL param reading
import dynamic from 'next/dynamic';
import { Loader2, AlertTriangle, Monitor, FileText, CheckCircle2, ChevronRight, ChevronLeft, Info } from 'lucide-react';
import api from '@/lib/axios-config';
import { useAuthStore } from '@/store/useAuthStore';

// Dynamically import ForceGraph with SSR disabled
const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), {
    ssr: false,
    loading: () => <GraphLoader mode="3D" />
});

const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), {
    ssr: false,
    loading: () => <GraphLoader mode="2D" />
});

function GraphLoader({ mode }: { mode: string }) {
    return (
        <div className="flex flex-col items-center justify-center h-full text-zinc-500 bg-zinc-950">
            <Loader2 className="animate-spin mb-2" size={32} />
            <span className="text-sm font-medium">Initializing {mode} Vector Map...</span>
        </div>
    );
}

interface RetrievalChunk {
    rank: number;
    score: number;
    title: string;
    text: string;
    source_message_id: string;
}

interface DebugInfo {
    retrieval: {
        chunks: RetrievalChunk[];
    };
}

interface VectorNode {
    id: string;
    name: string;
    title?: string;
    content?: string;
    val?: number;
    group?: number;
    x?: number;
    y?: number;
    z?: number;
}

interface VectorLink {
    source: string;
    target: string;
    [key: string]: unknown;
}

interface VectorGraphData {
    nodes: VectorNode[];
    links: VectorLink[];
}

interface VectorMapViewProps {
    requestId?: string;
    projectId?: string;
}

export default function VectorMapView({ requestId, projectId }: VectorMapViewProps) {
    const fgRef = useRef<any>(null); // [v5.0] ForceGraph reference for camera control
    const searchParams = useSearchParams(); // [v5.0] Read URL params
    const highlightNodeId = searchParams?.get('nodeId'); // [v5.0] Auto-focus node ID
    const [data, setData] = useState<VectorGraphData>({ nodes: [], links: [] });
    const [use2D, setUse2D] = useState(false);
    const [renderError, setRenderError] = useState<string | null>(null);
    
    // [v4.2] Debug Info State
    const [debugInfo, setDebugInfo] = useState<DebugInfo | null>(null);
    const [loadingDebug, setLoadingDebug] = useState(false);
    const [debugError, setDebugError] = useState<string | null>(null);
    const [isPanelOpen, setIsPanelOpen] = useState(false); // [v4.2 UX] Panel Toggle
    const [selectedNode, setSelectedNode] = useState<any>(null); // [v5.0] Selected Node State
    const { token, user } = useAuthStore();
    const isAdmin = user?.role === 'super_admin';

    // Fetch Debug Info on requestId change
    useEffect(() => {
        const fetchDebugInfo = async () => {
            if (!requestId || !isAdmin || !token) {
                setDebugInfo(null);
                setIsPanelOpen(false);
                return;
            }

            setLoadingDebug(true);
            setDebugError(null);
            setIsPanelOpen(true); // Auto-open on new request
            try {
                // [v4.2 UX] Use query param and correct endpoint
                const response = await api.get(`/master/chat_debug`, {
                    params: { request_id: requestId },
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                const sortedChunks = response.data.debug_info.retrieval.chunks.sort((a: RetrievalChunk, b: RetrievalChunk) => b.score - a.score);
                setDebugInfo({ ...response.data.debug_info, retrieval: { chunks: sortedChunks } });
            } catch (err: any) {
                console.error("Failed to fetch debug info:", err);
                // [v4.2 UX] Silent Failure
                setDebugInfo(null); 
            } finally {
                setLoadingDebug(false);
            }
        };
        fetchDebugInfo();
    }, [requestId, isAdmin, token]);

    useEffect(() => {
        // Detect mobile or low-end device to prefer 2D
        if (typeof window !== 'undefined') {
            const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
            if (isMobile) {
                setUse2D(true);
            }
        }

        // [v4.2 FIX] Use actual project context instead of dummy "Encoder-Decoder"
        // Since we don't have vector coordinates, we'll create a "Knowledge Cloud" simulation
        const fetchContextGraph = async () => {
            try {
                const targetProject = projectId || 'system-master';
                const response = await api.get(`/projects/${targetProject}/knowledge-graph`);
                const gData = response.data;
                
                // Enhance data for 3D visualization
                const nodes = gData.nodes.map((n: any) => ({
                    ...n,
                    name: n.title || n.name || (n.content ? n.content.slice(0, 30) + '...' : n.id), // [v5.0] Label Fallback
                    group: n.type === 'Requirement' ? 1 : n.type === 'Decision' ? 2 : 3,
                    val: n.val || 5
                }));
                
                // Fallback if graph is empty (prevent empty canvas)
                if (nodes.length === 0) {
                    setData({
                        nodes: [{ id: "root", name: "Knowledge Base", val: 20, group: 1 }],
                        links: []
                    });
                } else {
                    setData({ nodes, links: gData.links });
                }
            } catch (err) {
                console.error("Failed to fetch graph context for vector map:", err);
                setData({ nodes: [], links: [] });
            }
        };
        
        fetchContextGraph();
    }, []);

    const handleNodeClick = (node: any) => {
        setSelectedNode(node);
        setIsPanelOpen(true);
        
        // Copy node title to clipboard
        const content = node.title || node.name || node.id;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(content)
                .catch(err => console.error('Failed to copy: ', err));
        }
    };

    const handleBackgroundClick = () => {
        setSelectedNode(null);
        setIsPanelOpen(false);
    };

    // [v5.0 Critical] Auto-select node from URL parameter
    useEffect(() => {
        if (highlightNodeId && data.nodes.length > 0 && fgRef.current) {
            console.log(`[v5.0 Vector] Searching for node: ${highlightNodeId} in ${data.nodes.length} nodes`);
            console.log(`[v5.0 Vector] First 5 node IDs: ${data.nodes.slice(0, 5).map((n: any) => n.id).join(', ')}`);
            
            const targetNode = data.nodes.find((n: any) => n.id === highlightNodeId);
            if (targetNode) {
                console.log(`[v5.0 Vector] ✅ Node FOUND: ${highlightNodeId}, name: ${targetNode.name}`);
                
                // Camera zoom animation (2D/3D compatible)
                if (use2D && fgRef.current.centerAt) {
                    fgRef.current.centerAt(targetNode.x, targetNode.y, 1000);
                    fgRef.current.zoom(3, 1000);
                } else if (!use2D && fgRef.current.cameraPosition) {
                    const distance = 200;
                    fgRef.current.cameraPosition(
                        { x: targetNode.x, y: targetNode.y, z: distance },
                        targetNode,
                        1000
                    );
                }
                
                // Select and highlight node
                setSelectedNode(targetNode);
                setIsPanelOpen(true);
            } else {
                console.warn(`[v5.0 Vector] ❌ Node NOT FOUND: ${highlightNodeId}`);
                console.warn(`[v5.0 Vector] Available node IDs (first 10): ${data.nodes.slice(0, 10).map((n: any) => n.id).join(', ')}`);
            }
        } else if (highlightNodeId && data.nodes.length === 0) {
            console.warn(`[v5.0 Vector] ⏳ Waiting for vector data to load... (nodeId: ${highlightNodeId})`);
        }
    }, [highlightNodeId, data.nodes, use2D]);

    if (renderError) {
        return (
            <div className="flex flex-col items-center justify-center h-full text-red-400 bg-zinc-950 p-6 text-center">
                <AlertTriangle size={48} className="mb-4 opacity-50" />
                <h3 className="text-lg font-bold mb-2">3D Rendering Failed</h3>
                <p className="text-sm text-zinc-500 max-w-xs mb-6">
                    Your device or browser might not support WebGL. 
                    {renderError}
                </p>
                <button 
                    onClick={() => { setUse2D(true); setRenderError(null); }}
                    className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-white rounded-lg transition-colors text-sm font-medium"
                >
                    Switch to 2D Mode
                </button>
            </div>
        );
    }

    return (
        <div className="w-full h-full bg-zinc-950 relative group flex overflow-hidden">
            {/* [v4.2] Audit Panel (Overlay) */}
            {/* [v4.2 UX] Toggleable Panel */}
            <div 
                className={`absolute top-0 left-0 bottom-0 z-20 w-96 bg-zinc-900/95 backdrop-blur-md border-r border-zinc-800 flex flex-col transition-transform duration-300 ease-in-out shadow-2xl ${
                    isPanelOpen ? 'translate-x-0' : '-translate-x-full'
                }`}
            >
                {/* Panel Toggle Button (Visible when closed) */}
                {!isPanelOpen && (
                    <button 
                        onClick={() => setIsPanelOpen(true)}
                        className="absolute -right-8 top-1/2 -translate-y-1/2 w-8 h-16 bg-zinc-800 border-y border-r border-zinc-700 rounded-r-lg flex items-center justify-center hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors shadow-lg z-30"
                        title="Open Audit Panel"
                    >
                        <ChevronRight size={16} />
                    </button>
                )}

                <div className="p-4 border-b border-zinc-800 flex items-center justify-between">
                    <h3 className="font-bold text-zinc-200 flex items-center gap-2">
                        <CheckCircle2 size={16} className="text-indigo-500" />
                        Source Audit
                    </h3>
                    <div className="flex items-center gap-2">
                        {loadingDebug && <Loader2 size={14} className="animate-spin text-zinc-500" />}
                        <button 
                            onClick={() => setIsPanelOpen(false)}
                            className="p-1 hover:bg-zinc-800 rounded text-zinc-400 hover:text-white transition-colors"
                        >
                            <ChevronLeft size={16} />
                        </button>
                    </div>
                </div>
                
                <div className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-thin scrollbar-thumb-zinc-700">
                    {/* [v5.0] Selected Node Detail View */}
                    {selectedNode && (
                        <div className="mb-6 p-4 rounded-lg bg-indigo-900/20 border border-indigo-500/30">
                            <h4 className="text-xs font-bold text-indigo-400 uppercase tracking-wider mb-2">
                                Selected Node
                            </h4>
                            <div className="flex items-center gap-2 mb-3">
                                <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-500 text-white">
                                    {selectedNode.type || 'Node'}
                                </span>
                                <span className="text-xs text-zinc-500 font-mono truncate">
                                    ID: {selectedNode.id}
                                </span>
                            </div>
                            <h3 className="text-lg font-bold text-white mb-2 leading-tight">
                                {selectedNode.title || selectedNode.name || selectedNode.id}
                            </h3>
                            <div className="text-sm text-zinc-300 space-y-2">
                                {/* [v5.0] Score Display for Vector Nodes */}
                                {(selectedNode as any).score != null && (
                                    <div className="flex items-center gap-2 mb-2 p-2 bg-indigo-500/10 border border-indigo-500/20 rounded">
                                        <span className="text-xs font-bold text-indigo-400">Similarity Score:</span>
                                        <span className="text-sm font-mono text-white">{((selectedNode as any).score).toFixed(4)}</span>
                                    </div>
                                )}
                                {selectedNode.content && (
                                    <div className="p-2 bg-black/20 rounded border border-white/5 whitespace-pre-wrap max-h-60 overflow-y-auto scrollbar-thin">
                                        {selectedNode.content}
                                    </div>
                                )}
                                {selectedNode.source_message_id && (
                                    <div className="text-[10px] text-zinc-500 font-mono pt-2 border-t border-white/5">
                                        Source: {selectedNode.source_message_id}
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {!loadingDebug && !selectedNode && (!debugInfo || !debugInfo.retrieval.chunks.length) && (
                        <div className="text-center text-zinc-500 text-sm py-10 flex flex-col items-center gap-2">
                            <FileText size={24} className="opacity-30" />
                            <span>
                                {requestId ? "No evidence found for this request." : "Click a node or select a response to view details."}
                            </span>
                        </div>
                    )}

                    {/* [v4.2] Retrieval Chunks (Debug Info) */}
                    {debugInfo?.retrieval.chunks && debugInfo.retrieval.chunks.length > 0 && (
                        <div className="mb-4">
                            <h4 className="text-xs font-bold text-emerald-400 uppercase tracking-wider mb-3">
                                Vector Search Results ({debugInfo.retrieval.chunks.length})
                            </h4>
                        </div>
                    )}

                    {debugInfo?.retrieval.chunks.map((chunk, idx) => (
                        <div 
                            key={idx} 
                            className={`p-3 rounded-lg border transition-all ${
                                idx === 0 
                                    ? 'bg-indigo-900/20 border-indigo-500/50 shadow-lg shadow-indigo-900/10' 
                                    : 'bg-zinc-800/50 border-zinc-700/50 hover:bg-zinc-800'
                            }`}
                        >
                            <div className="flex justify-between items-start mb-2">
                                <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${idx === 0 ? 'bg-indigo-500 text-white' : 'bg-zinc-700 text-zinc-400'}`}>
                                    #{chunk.rank}
                                </span>
                                <span className="text-[10px] font-mono text-zinc-500">
                                    Score: {chunk.score != null ? chunk.score.toFixed(4) : 'N/A'}
                                </span>
                            </div>
                            <h4 className="text-sm font-semibold text-zinc-200 mb-1 line-clamp-1" title={chunk.title}>
                                {chunk.title || "Untitled Chunk"}
                            </h4>
                            <details className="group">
                                <summary className="text-xs text-zinc-400 cursor-pointer list-none flex items-center gap-1 hover:text-zinc-300">
                                    <FileText size={10} />
                                    <span>View Content</span>
                                </summary>
                                <p className="mt-2 text-xs text-zinc-300 whitespace-pre-wrap bg-black/20 p-2 rounded border border-zinc-700/50 max-h-60 overflow-y-auto scrollbar-thin">
                                    {chunk.text}
                                </p>
                            </details>
                        </div>
                    ))}
                </div>
            </div>

            {/* 3D/2D Graph Canvas */}
            <div className="flex-1 relative transition-all duration-300 ease-in-out" style={{ marginLeft: isPanelOpen ? '24rem' : '0' }}>
                <div className="absolute top-4 left-4 z-10 flex gap-2">
                    <button 
                        onClick={() => setUse2D(!use2D)}
                        className="px-3 py-1.5 bg-black/50 backdrop-blur-md border border-zinc-800 rounded-md text-[10px] font-bold text-zinc-400 hover:text-white hover:border-zinc-600 transition-all flex items-center gap-2 uppercase tracking-widest"
                    >
                        <Monitor size={12} />
                        {use2D ? 'Switch to 3D' : 'Switch to 2D'}
                    </button>
                </div>

                {use2D ? (
                    <ForceGraph2D
                        ref={fgRef} // [v5.0] Enable camera control
                        graphData={data}
                        nodeLabel="name"
                        nodeColor={(node: any) => node.color || "#3b82f6"} // Use actual node colors
                        backgroundColor="#09090b"
                        onNodeClick={handleNodeClick}
                        onBackgroundClick={handleBackgroundClick}
                    />
                ) : (
                    <ForceGraph3D
                        ref={fgRef} // [v5.0] Enable camera control
                        graphData={data}
                        nodeLabel="name"
                        nodeColor={(node: any) => node.color || "#3b82f6"} // Use actual node colors
                        backgroundColor="#09090b"
                        linkOpacity={0.5}
                        nodeResolution={16}
                        onNodeClick={handleNodeClick}
                        onBackgroundClick={handleBackgroundClick}
                    />
                )}
            </div>
        </div>
    );
}
