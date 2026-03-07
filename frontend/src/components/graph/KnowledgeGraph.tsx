'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useSearchParams } from 'next/navigation'; // [v5.0] For URL param reading
import dynamic from 'next/dynamic';
import { useDomainStore } from '@/store/useDomainStore';
import { Loader2, Share2, Info, ChevronRight, ChevronLeft } from 'lucide-react';
import api from '@/lib/axios-config';
import { useAuthStore } from '@/store/useAuthStore';

// Dynamically import ForceGraph2D to avoid SSR issues
const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), {
    ssr: false,
    loading: () => <div className="flex items-center justify-center h-full text-zinc-500"><Loader2 className="animate-spin mr-2" /> Loading Graph...</div>
});

interface KnowledgeGraphProps {
    projectId?: string;
    requestId?: string; // [v4.2]
}

interface RetrievalChunk {
    rank: number;
    score: number;
    title: string;
    text: string;
    source_message_id: string;
}

interface GraphNode {
    id: string;
    name: string;
    title?: string;
    type: string;
    source_message_id?: string;
    color?: string; // Original color
    [key: string]: any;
}

export default function KnowledgeGraph({ projectId, requestId }: KnowledgeGraphProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const fgRef = useRef<any>(null); // [v5.0] ForceGraph reference for camera control
    const searchParams = useSearchParams(); // [v5.0] Read URL params
    const highlightNodeId = searchParams?.get('nodeId'); // [v5.0] Auto-focus node ID
    const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
    const currentDomain = useDomainStore((state) => state.currentDomain);
    const [data, setData] = useState<{ nodes: GraphNode[], links: any[] }>({ nodes: [], links: [] });
    const [loading, setLoading] = useState(false);
    
    // [v4.2] Audit State
    const [highlightedNodeIds, setHighlightedNodeIds] = useState<Set<string>>(new Set());
    const [highlightedNodes, setHighlightedNodes] = useState<GraphNode[]>([]);
    const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null); // [v5.0] Clicked Node State
    const [isPanelOpen, setIsPanelOpen] = useState(false); // [v4.2 UX] Panel Toggle
    const { token, user } = useAuthStore();
    const isAdmin = user?.role === 'super_admin';

    useEffect(() => {
        // Update dimensions on resize
        const updateDimensions = () => {
            if (containerRef.current) {
                setDimensions({
                    width: containerRef.current.clientWidth,
                    height: containerRef.current.clientHeight
                });
            }
        };

        window.addEventListener('resize', updateDimensions);
        updateDimensions();

        return () => window.removeEventListener('resize', updateDimensions);
    }, []);

    // Fetch Graph Data
    useEffect(() => {
        const fetchGraph = async () => {
            const effectiveProjectId = projectId || 'system-master';
            setLoading(true);
            try {
                const response = await api.get(`/projects/${effectiveProjectId}/knowledge-graph`);
                setData(response.data);
            } catch (err) {
                console.error("Failed to fetch knowledge graph:", err);
            } finally {
                setLoading(false);
            }
        };

        fetchGraph();
    }, [projectId, currentDomain]);

    // [v4.2] Fetch Debug Info & Highlight Nodes
    useEffect(() => {
        const fetchDebugAndHighlight = async () => {
            if (!requestId || !isAdmin || !token) {
                setHighlightedNodeIds(new Set());
                setHighlightedNodes([]);
                setIsPanelOpen(false);
                return;
            }

            try {
                setIsPanelOpen(true); // Auto-open on new request
                const response = await api.get(`/master/chat_debug`, {
                    params: { request_id: requestId },
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                
                const chunks: RetrievalChunk[] = response.data.debug_info.retrieval.chunks;

                // Identify highlighted IDs
                const sourceIds = new Set(chunks.map(c => c.source_message_id).filter(id => id));
                setHighlightedNodeIds(sourceIds);

                // Filter nodes for the list
                if (data.nodes.length > 0) {
                    const nodes = data.nodes.filter(n => sourceIds.has(n.id) || sourceIds.has(n.source_message_id || ''));
                    setHighlightedNodes(nodes);
                }

            } catch (err) {
                console.error("Failed to fetch debug info for graph:", err);
                setHighlightedNodeIds(new Set());
                setHighlightedNodes([]);
            }
        };

        if (requestId) {
            fetchDebugAndHighlight();
        } else {
            setHighlightedNodeIds(new Set());
            setHighlightedNodes([]);
        }
    }, [requestId, isAdmin, token, data.nodes.length]); 

    const handleNodeClick = (node: any) => {
        setSelectedNode(node); // [v5.0] Set selected node
        setIsPanelOpen(true); // Open panel on node click
        const content = node.title || node.name || node.id;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(content)
                .catch(err => console.error('Failed to copy: ', err));
        }
    };

    const handleBackgroundClick = () => {
        setSelectedNode(null); // Clear selection
        setIsPanelOpen(false); // Close panel on background click
    };

    // [v4.2] Styling Logic
    const getNodeColor = useCallback((node: any) => {
        // [v4.2 FIX] If no requestId, allow default group coloring (return undefined to let nodeAutoColorBy work, or return node.color)
        if (!requestId) return node.color; 
        
        const isHighlighted = highlightedNodeIds.has(node.id) || highlightedNodeIds.has(node.source_message_id || '');
        if (isHighlighted) return "#10b981"; // Emerald-500
        return "#3f3f46"; // Dimmed
    }, [requestId, highlightedNodeIds]);

    const getNodeVal = useCallback((node: any) => {
        const isHighlighted = highlightedNodeIds.has(node.id) || highlightedNodeIds.has(node.source_message_id || '');
        if (requestId && isHighlighted) return 10;
        if (requestId && !isHighlighted) return 1;
        return node.val || 5;
    }, [requestId, highlightedNodeIds]);

    const getNodeLabel = useCallback((node: any) => {
        let label = node.title || node.name || (node.content ? node.content.slice(0, 30) + '...' : node.id); // [v5.0] Label Fallback
        if (label.startsWith('kg-')) {
            label = label.substring(3, 11) + '...'; 
        }
        return label;
    }, []);

    // [v5.0 Critical] Auto-select node from URL parameter
    useEffect(() => {
        if (highlightNodeId && data.nodes.length > 0 && fgRef.current) {
            console.log(`[v5.0 Graph] Searching for node: ${highlightNodeId} in ${data.nodes.length} nodes`);
            console.log(`[v5.0 Graph] First 5 node IDs: ${data.nodes.slice(0, 5).map(n => n.id).join(', ')}`);
            
            const targetNode = data.nodes.find(n => n.id === highlightNodeId);
            if (targetNode) {
                console.log(`[v5.0 Graph] ✅ Node FOUND: ${highlightNodeId}, title: ${targetNode.title || targetNode.name}`);
                
                // Camera zoom animation
                fgRef.current.centerAt(targetNode.x, targetNode.y, 1000);
                fgRef.current.zoom(3, 1000);
                
                // Select and highlight node
                setSelectedNode(targetNode);
                setIsPanelOpen(true);
                
                // Add to highlight set
                setHighlightedNodeIds(new Set([targetNode.id]));
            } else {
                console.warn(`[v5.0 Graph] ❌ Node NOT FOUND: ${highlightNodeId}`);
                console.warn(`[v5.0 Graph] Available node IDs (first 10): ${data.nodes.slice(0, 10).map(n => n.id).join(', ')}`);
            }
        } else if (highlightNodeId && data.nodes.length === 0) {
            console.warn(`[v5.0 Graph] ⏳ Waiting for graph data to load... (nodeId: ${highlightNodeId})`);
        }
    }, [highlightNodeId, data.nodes]);

    if (loading && data.nodes.length === 0) {
        return (
            <div className="flex items-center justify-center h-full text-zinc-500 bg-zinc-950">
                <Loader2 className="animate-spin mr-2" /> Loading Knowledge Graph...
            </div>
        );
    }

    return (
        <div className="w-full h-full bg-zinc-950 relative group flex overflow-hidden">
            {/* [v4.2 UX] Toggleable Audit Panel */}
            <div 
                className={`absolute top-0 left-0 bottom-0 z-20 w-96 bg-zinc-900/95 backdrop-blur-md border-r border-zinc-800 flex flex-col transition-transform duration-300 ease-in-out shadow-2xl ${
                    isPanelOpen ? 'translate-x-0' : '-translate-x-full'
                }`}
            >
                {/* Toggle Handle */}
                {!isPanelOpen && (
                    <button 
                        onClick={() => setIsPanelOpen(true)}
                        className="absolute -right-8 top-1/2 -translate-y-1/2 w-8 h-16 bg-zinc-800 border-y border-r border-zinc-700 rounded-r-lg flex items-center justify-center hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors shadow-lg z-30"
                        title="Open Graph Panel"
                    >
                        <ChevronRight size={16} />
                    </button>
                )}

                <div className="p-4 border-b border-zinc-800 flex items-center justify-between">
                    <h3 className="font-bold text-zinc-200 flex items-center gap-2">
                        <Share2 size={16} className="text-emerald-500" />
                        Graph Audit
                    </h3>
                    <button 
                        onClick={() => setIsPanelOpen(false)}
                        className="p-1 hover:bg-zinc-800 rounded text-zinc-400 hover:text-white transition-colors"
                    >
                        <ChevronLeft size={16} />
                    </button>
                </div>
                
                <div className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-thin scrollbar-thumb-zinc-700">
                    {/* [v5.0] Selected Node Detail View */}
                    {selectedNode && (
                        <div className="mb-6 p-4 rounded-lg bg-indigo-900/20 border border-indigo-500/30">
                            <h4 className="text-xs font-bold text-indigo-400 uppercase tracking-wider mb-2">
                                Selected Node Detail
                            </h4>
                            <div className="flex items-center gap-2 mb-3">
                                <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-500 text-white">
                                    {selectedNode.type}
                                </span>
                                <span className="text-xs text-zinc-500 font-mono truncate">
                                    ID: {selectedNode.id}
                                </span>
                            </div>
                            <h3 className="text-lg font-bold text-white mb-2 leading-tight">
                                {selectedNode.title || selectedNode.name}
                            </h3>
                            <div className="text-sm text-zinc-300 space-y-2">
                                {selectedNode.content && (
                                    <div className="p-2 bg-black/20 rounded border border-white/5 whitespace-pre-wrap">
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

                    {!requestId && !selectedNode && (
                        <div className="text-center text-zinc-500 text-sm py-10 flex flex-col items-center gap-2">
                            <Info size={24} className="opacity-30" />
                            <span>Select a chat response to view graph context.</span>
                        </div>
                    )}

                    {requestId && highlightedNodes.length === 0 && !selectedNode && (
                        <div className="text-center text-zinc-500 text-sm py-10">
                            No directly linked nodes found for this response.
                        </div>
                    )}

                    {/* RAG Context List */}
                    {highlightedNodes.length > 0 && (
                        <>
                            <h4 className="text-xs font-bold text-emerald-500 uppercase tracking-wider mb-2 px-1">
                                RAG Context ({highlightedNodes.length})
                            </h4>
                            {highlightedNodes.map((node, idx) => (
                                <div 
                                    key={idx} 
                                    className={`p-3 rounded-lg border shadow-lg transition-all cursor-pointer ${
                                        selectedNode?.id === node.id 
                                            ? 'bg-emerald-900/40 border-emerald-400 ring-1 ring-emerald-400' 
                                            : 'bg-emerald-900/20 border-emerald-500/50 hover:bg-emerald-900/30'
                                    }`}
                                    onClick={() => {
                                        setSelectedNode(node); // Sync selection
                                        // Optional: Center graph on node
                                    }}
                                >
                                    <div className="flex justify-between items-start mb-1">
                                        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-500 text-white uppercase">
                                            {node.type || 'Node'}
                                        </span>
                                    </div>
                                    <h4 className="text-sm font-semibold text-zinc-200 mb-1" title={node.title || node.name}>
                                        {getNodeLabel(node)}
                                    </h4>
                                    {node.source_message_id && (
                                        <p className="text-[10px] text-zinc-500 font-mono mt-2 truncate">
                                            Src: {node.source_message_id}
                                        </p>
                                    )}
                                </div>
                            ))}
                        </>
                    )}
                </div>
            </div>

            <div ref={containerRef} className="flex-1 overflow-hidden relative transition-all duration-300 ease-in-out" style={{ marginLeft: isPanelOpen ? '24rem' : '0' }}>
                <ForceGraph2D
                    ref={fgRef} // [v5.0] Enable camera control
                    width={dimensions.width - (isPanelOpen ? 384 : 0)} 
                    height={dimensions.height}
                    graphData={data}
                    nodeLabel={getNodeLabel} 
                    nodeColor={getNodeColor}
                    nodeRelSize={6}
                    nodeVal={getNodeVal}
                    nodeAutoColorBy="type" // [v4.2 FIX] Enable default grouping by type
                    linkColor={() => '#6366f1'} // [v5.0 FIX] Indigo-500 for visibility
                    linkWidth={2} // [v5.0] Make links more visible
                    linkDirectionalArrowLength={3.5} // [v5.0] Show relationship direction
                    linkDirectionalArrowRelPos={1} // Arrow at the end of link
                    linkLabel={(link: any) => link.type || 'RELATES_TO'} // [v5.0] Show relationship type on hover
                    backgroundColor="#09090b"
                    onNodeClick={handleNodeClick}
                    onBackgroundClick={handleBackgroundClick} // Close panel
                    cooldownTicks={100}
                />
            </div>
        </div>
    );
}
