import { X, MessageSquare, Info } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Button } from './ui/button';
import { useState } from 'react';
import type { Request } from '../types';
import { renderMarkdownSafe } from '../lib/markdown';

interface RequestDetailsModalProps {
    request: Request;
    onClose: () => void;
    // We reuse the components from Requests.tsx logic, but since they aren't exported,
    // we'll pass them in or redefine them if they are small.
    // Actually, let's just implement a standalone version for the modal.
    RequestStateList: React.ComponentType<{ request: Request }>;
}

export function RequestDetailsModal({ request, onClose, RequestStateList }: RequestDetailsModalProps) {
    const [activeTab, setActiveTab] = useState<'status' | 'conversation'>('status');

    return (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-100 p-4 animate-in fade-in duration-200">
            <Card className="w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col bg-white shadow-2xl ring-1 ring-black/5 animate-in zoom-in-95 duration-200">
                <CardHeader className="shrink-0 border-b border-gray-100 flex flex-row items-center justify-between py-4">
                    <div className="flex flex-col">
                        <CardTitle className="text-xl font-bold text-gray-900">{request.title}</CardTitle>
                        <p className="text-xs text-gray-500 font-medium uppercase tracking-wider mt-1">
                            ID: {request.id} • Type: {request.type.replace(/_/g, ' ')}
                        </p>
                    </div>
                    <Button
                        variant="ghost"
                        onClick={onClose}
                        className="rounded-full hover:bg-gray-100 transition-colors h-10 w-10 p-0"
                    >
                        <X className="w-5 h-5" />
                    </Button>
                </CardHeader>

                <div className="flex border-b border-gray-100">
                    <button
                        onClick={() => setActiveTab('status')}
                        className={`flex-1 py-3 text-sm font-semibold transition-all ${activeTab === 'status'
                            ? 'text-primary border-b-2 border-primary bg-primary/5'
                            : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                            } flex items-center justify-center gap-2`}
                    >
                        <Info className="w-4 h-4" />
                        Request Status
                    </button>
                    <button
                        onClick={() => setActiveTab('conversation')}
                        className={`flex-1 py-3 text-sm font-semibold transition-all ${activeTab === 'conversation'
                            ? 'text-primary border-b-2 border-primary bg-primary/5'
                            : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                            } flex items-center justify-center gap-2`}
                    >
                        <MessageSquare className="w-4 h-4" />
                        Conversation History
                    </button>
                </div>

                <CardContent className="flex-1 overflow-y-auto p-6 bg-gray-50/30">
                    {activeTab === 'status' ? (
                        <RequestStateList request={request} />
                    ) : (
                        <div className="space-y-4">
                            {request.conversation && request.conversation.length > 0 ? (
                                request.conversation.map((message, idx) => {
                                    const msgObj = message as unknown as Record<string, unknown>;
                                    const isUser = message.type === 'user' || msgObj.role === 'user';
                                    const isAgent = message.type === 'agent' || msgObj.role === 'assistant' || msgObj.role === 'agent';

                                    return (
                                        <div
                                            key={idx}
                                            className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
                                        >
                                            <div
                                                className={`max-w-[85%] rounded-2xl px-4 py-3 shadow-sm ${isUser
                                                    ? 'bg-primary text-white'
                                                    : 'bg-white text-gray-900 border border-gray-100'
                                                    }`}
                                            >
                                                {isAgent ? (
                                                    <div
                                                        className="text-sm leading-relaxed prose prose-sm agent-prose max-w-none text-current"
                                                        dangerouslySetInnerHTML={{ __html: renderMarkdownSafe(message.content) }}
                                                    />
                                                ) : (
                                                    <p className="text-sm leading-relaxed">{message.content}</p>
                                                )}
                                                <p className={`text-[10px] mt-2 opacity-70 font-medium`}>
                                                    {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                                </p>
                                            </div>
                                        </div>
                                    );
                                })
                            ) : (
                                <div className="text-center py-12 text-gray-400 italic">
                                    No conversation history available.
                                </div>
                            )}
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
