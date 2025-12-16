import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles, ArrowRight, Send, ExternalLink } from 'lucide-react';
import { Input } from '../components/ui/input';
import { Textarea } from '../components/ui/textarea';
import { Button } from '../components/ui/button';
import type { ChatMessage, ConversationState } from '../types';
import { callAgent } from '../services/api';

// Determine which form route to use based on conversation (fallback only for error cases)
const determineFormRoute = (query: string, _answers: Record<string, string | string[]>, context?: { type: 'paas' | 'daas'; title: string }): { path: string; title: string } => {
  const lowerQuery = query.toLowerCase();
  
  if (lowerQuery.includes('workspace access') || (lowerQuery.includes('workspace') && lowerQuery.includes('access'))) {
    return { path: '/paas/workspace-access', title: 'Get Workspace Access' };
  } else if (lowerQuery.includes('catalog') || lowerQuery.includes('schema') || lowerQuery.includes('table')) {
    if (lowerQuery.includes('create') || lowerQuery.includes('new')) {
      return { path: '/paas/request-catalog', title: 'Create Catalog/Schema/Table' };
    } else {
      return { path: '/paas/request-access', title: 'Request Data Access' };
    }
  } else if (lowerQuery.includes('data access')) {
    return { path: '/paas/request-access', title: 'Request Data Access' };
  } else if (lowerQuery.includes('workspace') && (lowerQuery.includes('provision') || lowerQuery.includes('new'))) {
    return { path: '/paas/provision-workspace', title: 'Provision New Workspace' };
  } else if (lowerQuery.includes('service principal')) {
    return { path: '/paas/service-principal', title: 'Provision Service Principal' };
  } else if (lowerQuery.includes('marketplace') || lowerQuery.includes('certification')) {
    return { path: '/paas/marketplace', title: 'Marketplace Certification' };
  } else if (lowerQuery.includes('rest api') || (lowerQuery.includes('api') && !lowerQuery.includes('batch'))) {
    return { path: '/daas/rest-api', title: 'Request REST API Access' };
  } else if (lowerQuery.includes('batch') || lowerQuery.includes('delta sharing')) {
    return { path: '/daas/batch-data', title: 'Request Batch Data Access' };
  } else if (context?.type === 'paas') {
    // Default PAAS routes based on context
    if (context.title.includes('Workspace Access')) {
      return { path: '/paas/workspace-access', title: 'Get Workspace Access' };
    } else if (context.title.includes('Catalog')) {
      return { path: '/paas/request-catalog', title: 'Create Catalog/Schema/Table' };
    } else if (context.title.includes('Data Access')) {
      return { path: '/paas/request-access', title: 'Request Data Access' };
    } else if (context.title.includes('Provision') && context.title.includes('Workspace')) {
      return { path: '/paas/provision-workspace', title: 'Provision New Workspace' };
    } else if (context.title.includes('Service Principal')) {
      return { path: '/paas/service-principal', title: 'Provision Service Principal' };
    } else if (context.title.includes('Marketplace')) {
      return { path: '/paas/marketplace', title: 'Marketplace Certification' };
    }
  } else if (context?.type === 'daas') {
    if (context.title.includes('REST API')) {
      return { path: '/daas/rest-api', title: 'Request REST API Access' };
    } else if (context.title.includes('Batch')) {
      return { path: '/daas/batch-data', title: 'Request Batch Data Access' };
    }
  }
  
  // Default fallback
  return { path: '/paas/request-access', title: 'Request Data Access' };
};

export function Home() {
  const [query, setQuery] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [conversationState, setConversationState] = useState<ConversationState | null>(null);
  const navigate = useNavigate();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Clear any existing conversation state from localStorage on mount (fresh start on refresh)
  useEffect(() => {
    // Clear conversation state but keep form prefill data
    const keysToRemove: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key === 'edas_hub_conversation_state') {
        keysToRemove.push(key);
      }
    }
    keysToRemove.forEach(key => localStorage.removeItem(key));
  }, []);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [conversationState?.messages, conversationState?.currentQuestionIndex]);

  // Focus input when conversation starts
  useEffect(() => {
    if (conversationState && !conversationState.showConfirmation) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [conversationState?.currentQuestionIndex, conversationState?.showConfirmation]);


  const handleInitialSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isProcessing) return;

    const initialQuery = query.trim();
    setIsProcessing(true);
    
    // Create initial user message
    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      type: 'user',
      content: initialQuery,
      timestamp: new Date(),
    };

    try {
      // Call the real agent endpoint
      const agentResponse = await callAgent({
        query: initialQuery,
        conversation_history: conversationState?.messages?.map(m => ({
          id: m.id,
          type: m.type,
          content: m.content,
          timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : (typeof m.timestamp === 'string' ? m.timestamp : new Date().toISOString()),
        })),
        context: conversationState?.context,
      });

      // Use only agent-provided questions (no fallback)
      const followUpQuestions = agentResponse.follow_up_questions || [];
      
      // Create agent message from response - show actual message or indicate waiting
      let agentMessageContent = agentResponse.message;
      if (!agentMessageContent || !agentMessageContent.trim()) {
        // If no message, the agent might be processing - show a helpful message
        agentMessageContent = "I'm processing your request. Please wait...";
      }
      
      const agentMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        type: 'agent',
        content: agentMessageContent,
        timestamp: new Date(),
      };

      // Create messages array
      const messages: ChatMessage[] = [userMessage, agentMessage];
      
      // Add first question if agent provided one
      if (followUpQuestions.length > 0) {
        messages.push({
          id: (Date.now() + 2).toString(),
          type: 'agent',
          content: followUpQuestions[0].question,
          timestamp: new Date(),
        });
      }

      // Determine if we should show confirmation (form route ready)
      const showConfirmation = !agentResponse.requires_more_info && (agentResponse.form_route !== null || agentResponse.form_prefill_data !== undefined);

      setConversationState({
        initialQuery,
        messages,
        currentQuestionIndex: 0,
        followUpQuestions,
        answers: {},
        agentActions: [],
        showConfirmation,
        formRoute: agentResponse.form_route || undefined,
        formPrefillData: agentResponse.form_prefill_data,
        context: conversationState?.context,
      });
    } catch (error) {
      console.error('Error calling agent:', error);
      // Show error message - no fallback questions
      const messages: ChatMessage[] = [
        userMessage,
        {
          id: (Date.now() + 1).toString(),
          type: 'agent',
          content: error instanceof Error 
            ? `I'm having trouble connecting to the agent service. ${error.message}. Please try again.`
            : "I'm having trouble connecting to the agent service. Please try again.",
          timestamp: new Date(),
        },
      ];

      setConversationState({
        initialQuery,
        messages,
        currentQuestionIndex: 0,
        followUpQuestions: [], // No questions on error
        answers: {},
        agentActions: [],
        showConfirmation: false,
        context: conversationState?.context,
      });
    }
    
    setQuery('');
    setIsProcessing(false);
  };

  const handleAnswerSubmit = async (questionId: string, answer: string | string[]) => {
    if (!conversationState || isProcessing) return;

    setIsProcessing(true);

    const updatedAnswers = {
      ...conversationState.answers,
      [questionId]: answer,
    };

    const answerMessage: ChatMessage = {
      id: Date.now().toString(),
      type: 'user',
      content: Array.isArray(answer) ? answer.join(', ') : answer,
      timestamp: new Date(),
    };

    try {
      // Call the agent with the answer to get next question or form route
      const agentResponse = await callAgent({
        query: `Answer to "${conversationState.followUpQuestions[conversationState.currentQuestionIndex]?.question}": ${Array.isArray(answer) ? answer.join(', ') : answer}`,
        conversation_history: conversationState.messages.map(m => ({
          id: m.id,
          type: m.type,
          content: m.content,
          timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : (typeof m.timestamp === 'string' ? m.timestamp : new Date().toISOString()),
        })),
        context: conversationState.context,
      });

      // Use only agent-provided questions (no fallback)
      const followUpQuestions = agentResponse.follow_up_questions || [];
      const nextIndex = agentResponse.follow_up_questions ? 0 : conversationState.currentQuestionIndex + 1;
      const hasMoreQuestions = agentResponse.requires_more_info && followUpQuestions.length > 0;

      // Create agent message from response
      const agentMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        type: 'agent',
        content: agentResponse.message || "Thank you for that information.",
        timestamp: new Date(),
      };

      const messages: ChatMessage[] = [...conversationState.messages, answerMessage, agentMessage];

      // Add next question if agent provided one
      if (hasMoreQuestions && followUpQuestions.length > 0) {
        const nextQuestion = followUpQuestions[0];
        messages.push({
          id: (Date.now() + 2).toString(),
          type: 'agent',
          content: nextQuestion.question,
          timestamp: new Date(),
        });
      }

      // Determine if we should show confirmation (form route ready)
      const showConfirmation = !agentResponse.requires_more_info && (agentResponse.form_route !== null || agentResponse.form_prefill_data !== undefined);

      setConversationState({
        ...conversationState,
        messages,
        currentQuestionIndex: agentResponse.follow_up_questions ? 0 : nextIndex, // Reset if new questions
        followUpQuestions: followUpQuestions,
        answers: updatedAnswers,
        agentActions: [],
        showConfirmation,
        formRoute: agentResponse.form_route || conversationState.formRoute,
        formPrefillData: agentResponse.form_prefill_data || conversationState.formPrefillData,
      });
    } catch (error) {
      console.error('Error calling agent:', error);
      // Show error message - no fallback questions
      const agentMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        type: 'agent',
        content: error instanceof Error 
          ? `I'm having trouble processing your answer. ${error.message}. Please try again.`
          : "I'm having trouble processing your answer. Please try again.",
        timestamp: new Date(),
      };

      setConversationState({
        ...conversationState,
        messages: [...conversationState.messages, answerMessage, agentMessage],
        answers: updatedAnswers,
      });
    }
    
    setIsProcessing(false);
  };

  const getButtonLabel = (path: string | undefined): string => {
    if (!path) return 'Continue';
    
    // Determine button label based on route
    if (path.startsWith('/paas/') || path.startsWith('/daas/')) {
      return 'Proceed to pre-filled form';
    } else if (path.includes('/community/links') || path === '/community-links') {
      return 'Go to community links';
    } else if (path.includes('/community/assets') || path === '/reusable-assets') {
      return 'View reusable assets';
    } else if (path.includes('/community/training') || path === '/training') {
      return 'Go to training';
    } else if (path.includes('/community/events') || path === '/events') {
      return 'View events';
    } else {
      // Fallback: try to extract meaningful label from path
      const pathParts = path.split('/').filter(Boolean);
      if (pathParts.length > 0) {
        const lastPart = pathParts[pathParts.length - 1];
        return `Go to ${lastPart.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}`;
      }
      return 'Continue';
    }
  };

  const handleGoToForm = () => {
    if (!conversationState?.formRoute?.path) {
      console.error('No form route path available');
      return;
    }
    
    const routePath = conversationState.formRoute.path;
    
    // Store prefilled data in localStorage before navigating
    // Use form_prefill_data from agent if available, otherwise use answers
    const prefillData = conversationState.formPrefillData || conversationState.answers;
    if (prefillData && Object.keys(prefillData).length > 0) {
      localStorage.setItem(`form_prefill_${routePath}`, JSON.stringify(prefillData));
    }
    
    // Clear conversation state
    // Conversation state is not persisted, so no need to remove from localStorage
    setConversationState(null);
    
    // Navigate to the route
    navigate(routePath);
  };


  // If we have a conversation in progress, show chat interface
  if (conversationState) {
    const currentQuestion = conversationState.followUpQuestions[conversationState.currentQuestionIndex];
    const allQuestionsAnswered = conversationState.currentQuestionIndex >= conversationState.followUpQuestions.length;

    return (
      <div className="flex flex-col h-[calc(100vh-200px)] relative">
        {/* Background gradient */}
        <div className="absolute inset-0 bg-gradient-to-br from-primary/5 via-transparent to-primary/10 pointer-events-none" />
        
        <div className="text-center mb-6 relative z-10">
          <div className="flex items-center justify-center gap-3 mb-3">
            <div className="p-2 bg-gradient-to-br from-primary to-primary/80 rounded-xl shadow-md">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <h1 className="text-3xl font-bold bg-gradient-to-r from-gray-900 to-gray-700 bg-clip-text text-transparent">
              {conversationState.context?.title || 'EDAS Hub'}
            </h1>
          </div>
        </div>

        <div className="relative flex-1 flex flex-col">
          {/* Glow effect */}
          <div className="absolute -inset-1 bg-gradient-to-r from-primary/20 to-primary/10 rounded-3xl blur-xl opacity-30" />
          
          <div className="relative flex-1 flex flex-col bg-white/80 backdrop-blur-sm rounded-3xl shadow-2xl border border-gray-200/50 overflow-hidden">
            <div className="flex-1 flex flex-col p-6 overflow-hidden">
              {/* Messages */}
              <div className="flex-1 overflow-y-auto space-y-4 mb-4 pr-2 custom-scrollbar">
                {conversationState.messages.map((message) => (
                  <div
                    key={message.id}
                    className={`flex ${message.type === 'user' ? 'justify-end' : 'justify-start'} animate-in fade-in slide-in-from-bottom-2 duration-300`}
                  >
                    <div
                      className={`max-w-[80%] rounded-2xl px-4 py-3 shadow-sm ${
                        message.type === 'user'
                          ? 'bg-gradient-to-br from-primary to-primary/90 text-white'
                          : 'bg-gray-50 text-gray-900 border border-gray-200/50'
                      }`}
                    >
                      {message.type === 'agent' ? (
                        <div 
                          className="text-sm leading-relaxed prose prose-sm max-w-none [&_a]:text-blue-600 [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:text-blue-700 [&_a:visited]:text-purple-600"
                          dangerouslySetInnerHTML={{ __html: message.content }}
                        />
                      ) : (
                        <p className="text-sm leading-relaxed">{message.content}</p>
                      )}
                    </div>
                  </div>
                ))}
              
              {/* Form Link - Show button inline with the last agent message that triggered confirmation */}
              {conversationState.showConfirmation && conversationState.formRoute && (
                <div className="space-y-4 mt-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                  <div className="bg-gradient-to-br from-blue-50 to-primary/5 border border-blue-200/50 rounded-2xl p-5 shadow-sm">
                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <Button
                          onClick={handleGoToForm}
                          className="flex items-center gap-2 rounded-xl shadow-md hover:shadow-lg transition-all duration-200"
                        >
                          <ExternalLink className="w-4 h-4" />
                          {getButtonLabel(conversationState.formRoute?.path)}
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>
              )}
              
              <div ref={messagesEndRef} />
            </div>

            {/* Input Area */}
            {/* Show input for specific question types */}
            {!allQuestionsAnswered && !conversationState.showConfirmation && currentQuestion && (
              <div className="border-t border-gray-200/50 pt-5 mt-4">
                <div className="space-y-3">
                  {currentQuestion.type === 'text' && (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        const input = e.currentTarget.querySelector('input') as HTMLInputElement;
                        if (input.value.trim()) {
                          handleAnswerSubmit(currentQuestion.id, input.value.trim());
                          input.value = '';
                        }
                      }}
                    >
                      <div className="flex gap-2">
                        <Input
                          type="text"
                          placeholder="Type your answer..."
                          required={currentQuestion.required}
                          className="flex-1 rounded-xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200"
                          disabled={isProcessing}
                        />
                        <Button 
                          type="submit" 
                          disabled={isProcessing}
                          className="rounded-xl shadow-md hover:shadow-lg transition-all duration-200"
                        >
                          <Send className="w-4 h-4" />
                        </Button>
                      </div>
                    </form>
                  )}
                  
                  {currentQuestion.type === 'radio' && (
                    <div className="space-y-2">
                      {currentQuestion.options?.map((option) => (
                        <button
                          key={option}
                          onClick={() => handleAnswerSubmit(currentQuestion.id, option)}
                          className="w-full text-left px-4 py-3 bg-gray-50 hover:bg-gradient-to-r hover:from-primary/10 hover:to-primary/5 rounded-xl border border-gray-200/50 hover:border-primary/30 transition-all duration-200 hover:shadow-sm text-sm"
                        >
                          {option}
                        </button>
                      ))}
                    </div>
                  )}
                  
                  {currentQuestion.type === 'multi-select' && (
                    <div className="space-y-2">
                      {currentQuestion.options?.map((option) => {
                        const isSelected = Array.isArray(conversationState.answers[currentQuestion.id]) &&
                          conversationState.answers[currentQuestion.id].includes(option);
                        return (
                          <button
                            key={option}
                            onClick={() => {
                              const current = Array.isArray(conversationState.answers[currentQuestion.id])
                                ? conversationState.answers[currentQuestion.id] as string[]
                                : [];
                              const updated = isSelected
                                ? current.filter(o => o !== option)
                                : [...current, option];
                              handleAnswerSubmit(currentQuestion.id, updated);
                            }}
                            className={`w-full text-left px-4 py-3 rounded-xl border transition-all duration-200 text-sm ${
                              isSelected
                                ? 'bg-gradient-to-r from-primary to-primary/90 text-white border-primary shadow-md'
                                : 'bg-gray-50 hover:bg-gradient-to-r hover:from-primary/10 hover:to-primary/5 border-gray-200/50 hover:border-primary/30 hover:shadow-sm'
                            }`}
                          >
                            {option}
                          </button>
                        );
                      })}
                      {Array.isArray(conversationState.answers[currentQuestion.id]) &&
                        conversationState.answers[currentQuestion.id].length > 0 && (
                        <Button
                          onClick={() => {
                            const answer = conversationState.answers[currentQuestion.id];
                            if (Array.isArray(answer) && answer.length > 0) {
                              // Move to next question
                              const nextIndex = conversationState.currentQuestionIndex + 1;
                              if (nextIndex < conversationState.followUpQuestions.length) {
                                const nextQuestion = conversationState.followUpQuestions[nextIndex];
                                const agentMessage: ChatMessage = {
                                  id: Date.now().toString(),
                                  type: 'agent',
                                  content: nextQuestion.question,
                                  timestamp: new Date(),
                                };
                                setConversationState({
                                  ...conversationState,
                                  messages: [...conversationState.messages, agentMessage],
                                  currentQuestionIndex: nextIndex,
                                });
                              } else {
                                // All questions answered, determine form route
                                const formRoute = determineFormRoute(
                                  conversationState.initialQuery,
                                  conversationState.answers,
                                  conversationState.context
                                );
                                
                                // Store prefilled data in localStorage for the form page
                                localStorage.setItem(`form_prefill_${formRoute.path}`, JSON.stringify(conversationState.answers));
                                
                                const agentMessage: ChatMessage = {
                                  id: Date.now().toString(),
                                  type: 'agent',
                                  content: `I have prefilled the correct form based on your input. Click the link below to review and submit.`,
                                  timestamp: new Date(),
                                };
                                setConversationState({
                                  ...conversationState,
                                  messages: [...conversationState.messages, agentMessage],
                                  currentQuestionIndex: nextIndex,
                                  agentActions: [],
                                  showConfirmation: true,
                                  formRoute,
                                });
                              }
                            }
                          }}
                          className="w-full mt-3 rounded-xl shadow-md hover:shadow-lg transition-all duration-200"
                        >
                          Continue
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
            
            {/* General text input - always available when conversation is active and no specific question */}
            {!conversationState.showConfirmation && (!currentQuestion || allQuestionsAnswered) && (
              <div className="border-t border-gray-200/50 pt-5 mt-4">
                <form
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const textarea = e.currentTarget.querySelector('textarea') as HTMLTextAreaElement;
                    const userMessage = textarea.value.trim();
                    if (userMessage && !isProcessing) {
                      setIsProcessing(true);
                      try {
                        // Call agent with the free-form message
                        const agentResponse = await callAgent({
                          query: userMessage,
                          conversation_history: conversationState.messages.map(m => ({
                            id: m.id,
                            type: m.type,
                            content: m.content,
                            timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : (typeof m.timestamp === 'string' ? m.timestamp : new Date().toISOString()),
                          })),
                          context: conversationState.context,
                        });

                        const userMsg: ChatMessage = {
                          id: Date.now().toString(),
                          type: 'user',
                          content: userMessage,
                          timestamp: new Date(),
                        };

                        const agentMsg: ChatMessage = {
                          id: (Date.now() + 1).toString(),
                          type: 'agent',
                          content: agentResponse.message || "I understand. Let me help you with that.",
                          timestamp: new Date(),
                        };

                        const followUpQuestions = agentResponse.follow_up_questions || [];
                        const messages: ChatMessage[] = [...conversationState.messages, userMsg, agentMsg];

                        // Add first question if agent provided one
                        if (followUpQuestions.length > 0) {
                          messages.push({
                            id: (Date.now() + 2).toString(),
                            type: 'agent',
                            content: followUpQuestions[0].question,
                            timestamp: new Date(),
                          });
                        }

                        const showConfirmation = !agentResponse.requires_more_info && (agentResponse.form_route !== null || agentResponse.form_prefill_data !== undefined);

                        setConversationState({
                          ...conversationState,
                          messages,
                          currentQuestionIndex: followUpQuestions.length > 0 ? 0 : conversationState.currentQuestionIndex,
                          followUpQuestions,
                          showConfirmation,
                          formRoute: agentResponse.form_route || conversationState.formRoute,
                          formPrefillData: agentResponse.form_prefill_data || conversationState.formPrefillData,
                        });

                        // Store prefilled data if available (prefer form_prefill_data from agent)
                        if (agentResponse.form_route) {
                          const prefillData = agentResponse.form_prefill_data || conversationState.answers;
                          if (prefillData && Object.keys(prefillData).length > 0) {
                            localStorage.setItem(`form_prefill_${agentResponse.form_route.path}`, JSON.stringify(prefillData));
                          }
                        }

                        textarea.value = '';
                      } catch (error) {
                        console.error('Error calling agent:', error);
                        const errorMsg: ChatMessage = {
                          id: (Date.now() + 1).toString(),
                          type: 'agent',
                          content: error instanceof Error 
                            ? `I'm having trouble processing your message. ${error.message}. Please try again.`
                            : "I'm having trouble processing your message. Please try again.",
                          timestamp: new Date(),
                        };
                        setConversationState({
                          ...conversationState,
                          messages: [...conversationState.messages, {
                            id: Date.now().toString(),
                            type: 'user',
                            content: userMessage,
                            timestamp: new Date(),
                          }, errorMsg],
                        });
                      } finally {
                        setIsProcessing(false);
                      }
                    }
                  }}
                >
                  <div className="flex gap-2">
                    <Textarea
                      ref={inputRef}
                      placeholder="Type your message..."
                      className="flex-1 rounded-xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200 min-h-[36px] max-h-[100px]"
                      disabled={isProcessing}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          const form = e.currentTarget.closest('form');
                          if (form && !isProcessing) {
                            form.requestSubmit();
                          }
                        }
                      }}
                    />
                    <Button 
                      type="submit" 
                      disabled={isProcessing}
                      className="rounded-xl shadow-md hover:shadow-lg transition-all duration-200 self-end"
                    >
                      <Send className="w-4 h-4" />
                    </Button>
                  </div>
                </form>
              </div>
            )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Initial state - show input form
  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-200px)] relative">
      <div className="w-full max-w-3xl">
        <div className="text-center mb-12">
          <div className="flex items-center justify-center gap-3 mb-6">
            <div className="p-3 bg-gradient-to-br from-primary to-primary/80 rounded-2xl shadow-lg">
              <Sparkles className="w-8 h-8 text-white" />
            </div>
            <h1 className="text-5xl font-bold bg-gradient-to-r from-gray-900 to-gray-700 bg-clip-text text-transparent">
              EDAS Hub
            </h1>
          </div>
          <p className="text-lg text-gray-600 max-w-xl mx-auto">
            Your intelligent assistant for EDAS self-service requests. Tell me what you need, and I'll guide you through the process.
          </p>
        </div>

        <div className="relative">
          {/* Glow effect */}
          <div className="absolute -inset-1 bg-gradient-to-r from-primary/20 to-primary/10 rounded-3xl blur-xl opacity-30" />
          
          <div className="relative bg-white/90 backdrop-blur-sm rounded-3xl shadow-xl border border-gray-200/50 overflow-hidden">
            <div className="p-8 md:p-10">
              <form onSubmit={handleInitialSubmit} className="space-y-4">
                <div className="relative group">
                  <div className="absolute inset-0 bg-gradient-to-r from-primary/10 to-transparent rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                  <Textarea
                    ref={inputRef}
                    placeholder="What do you need to do today?"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    className="text-base py-2.5 pr-14 rounded-2xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200 bg-white/90 backdrop-blur-sm min-h-[48px] max-h-[48px] resize-none overflow-hidden"
                    disabled={isProcessing}
                    rows={1}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        handleInitialSubmit(e);
                      }
                    }}
                  />
                  <Button
                    type="submit"
                    disabled={isProcessing || !query.trim()}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-xl shadow-lg hover:shadow-xl transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    size="sm"
                  >
                    {isProcessing ? (
                      <span className="animate-pulse px-3">Processing...</span>
                    ) : (
                      <ArrowRight className="w-4 h-4" />
                    )}
                  </Button>
                </div>
              </form>
              
              {/* Quick suggestions */}
              <div className="mt-6 pt-6 border-t border-gray-200/50">
                <p className="text-sm text-gray-500 mb-3">Try asking:</p>
                <div className="flex flex-wrap gap-2">
                  {[
                    { label: 'Get workspace access', query: "I need to get access to a workspace for my team" },
                    { label: 'Request data access', query: "I need access to some data tables for my project" },
                    { label: 'Create a new workspace', query: "I'd like to create a new workspace for our analytics team" },
                    { label: 'Provision service principal', query: "I need a service principal for my CI/CD pipeline" },
                    { label: 'Learn a new skill', query: "I want to learn new skills and improve my capabilities" },
                    { label: 'Find a community example', query: "I'm looking for examples of how others have solved similar problems" },
                    { label: 'Attend a workshop', query: "Are there any upcoming workshops or training sessions I can attend?" },
                    { label: 'Browse reusable assets', query: "I want to see what design patterns and templates are available" }
                  ].map(({ label, query }) => (
                    <button
                      key={label}
                      onClick={() => {
                        setQuery(query);
                        inputRef.current?.focus();
                      }}
                      className="px-4 py-2 text-sm text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-xl border border-gray-200/50 hover:border-primary/30 transition-all duration-200 hover:shadow-sm"
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
