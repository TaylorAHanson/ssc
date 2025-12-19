import { useMemo } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Position,
  type Node,
  type Edge,
} from 'reactflow';
import 'reactflow/dist/style.css';
import type { StateMachineState } from '../types';
import { CheckCircle2, Circle, Loader2 } from 'lucide-react';

interface RequestStatusFlowProps {
  stateMachine: StateMachineState;
  requestStatus?: string;
}

export function RequestStatusFlow({ stateMachine, requestStatus }: RequestStatusFlowProps) {
  const { nodes, edges } = useMemo(() => {
    const flowNodes: Node[] = [];
    const flowEdges: Edge[] = [];

    // Always start with "User Request" node
    const userRequestNode: Node = {
      id: 'user-request',
      type: 'default',
      position: { x: 0, y: 100 },
      data: {
        label: (
          <div className="flex flex-col items-center gap-2">
            <div className="w-12 h-12 rounded-full bg-green-500 flex items-center justify-center">
              <CheckCircle2 className="w-6 h-6 text-white" />
            </div>
            <span className="text-sm font-medium text-center max-w-[120px]">
              User Request
            </span>
          </div>
        ),
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
    flowNodes.push(userRequestNode);

    // Use new linear states structure
    if (stateMachine.states && stateMachine.states.length > 0) {
      // Simple linear flow - connect user request to first state
      const states = stateMachine.states;
      const allStatesComplete = states.every(state => state.isCompleted);
      
      states.forEach((state, index) => {
        const isCompleted = state.isCompleted;
        const isActive = state.isActive;
        const isPending = !isCompleted && !isActive;
        
        flowNodes.push({
          id: state.id,
          type: 'default',
          position: { x: 200 + index * 200, y: 100 },
          data: {
            label: (
              <div className="flex flex-col items-center gap-2">
                <div 
                  className={`w-12 h-12 rounded-full flex items-center justify-center ${
                    isCompleted ? 'bg-green-500' : isActive ? 'bg-blue-600 animate-pulse' : 'bg-gray-300'
                  }`}
                >
                  {isCompleted ? (
                    <CheckCircle2 className="w-6 h-6 text-white" />
                  ) : isActive ? (
                    <Loader2 className="w-6 h-6 text-white animate-spin" />
                  ) : (
                    <Circle className="w-6 h-6 text-white" />
                  )}
                </div>
                <span className={`text-sm font-medium text-center max-w-[120px] ${
                  isPending ? 'text-gray-400' : ''
                }`}>
                  {state.name}
                </span>
              </div>
            ),
          },
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
        });

        if (index === 0) {
          // Connect user request to first state
          flowEdges.push({
            id: 'e-user-request',
            source: 'user-request',
            target: state.id,
            style: {
              stroke: isCompleted ? '#10b981' : isActive ? '#3253DC' : '#d1d5db',
              strokeWidth: 2,
            },
            animated: isActive,
          });
        } else {
          flowEdges.push({
            id: `e${index - 1}-${index}`,
            source: states[index - 1].id,
            target: state.id,
            animated: isActive,
            style: {
              stroke: isCompleted ? '#10b981' : isActive ? '#3253DC' : '#d1d5db',
              strokeWidth: 2,
            },
          });
        }
      });

      // Add procedural steps after all states
      const lastStateX = 200 + (states.length - 1) * 200;
      const terraformStatus = allStatesComplete && requestStatus === 'provisioning' ? 'active' : 
                              requestStatus === 'completed' ? 'completed' : 'pending';
      const finishedStatus = requestStatus === 'completed' ? 'completed' : 'pending';
      
      const proceduralSteps = [
        { id: 'terraform-apply', name: 'Terraform Apply', status: terraformStatus },
        { id: 'finished', name: 'Finished', status: finishedStatus },
      ];

      proceduralSteps.forEach((step, index) => {
        const stepX = lastStateX + (index + 1) * 200;
        const isCompleted = step.status === 'completed';
        const isActive = step.status === 'active';
        const isPending = step.status === 'pending';

        flowNodes.push({
          id: step.id,
          type: 'default',
          position: { x: stepX, y: 100 },
          data: {
            label: (
              <div className="flex flex-col items-center gap-2">
                <div 
                  className={`w-12 h-12 rounded-full flex items-center justify-center ${
                    isCompleted ? 'bg-green-500' : isActive ? 'bg-blue-600 animate-pulse' : 'bg-gray-300'
                  }`}
                >
                  {isCompleted ? (
                    <CheckCircle2 className="w-6 h-6 text-white" />
                  ) : isActive ? (
                    <Loader2 className="w-6 h-6 text-white animate-spin" />
                  ) : (
                    <Circle className="w-6 h-6 text-white" />
                  )}
                </div>
                <span className={`text-sm font-medium text-center max-w-[120px] ${
                  isPending ? 'text-gray-400' : ''
                }`}>
                  {step.name}
                </span>
              </div>
            ),
          },
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
        });

        if (index === 0) {
          // Connect last state to first procedural step
          const lastState = states[states.length - 1];
          flowEdges.push({
            id: `e-${lastState.id}-terraform`,
            source: lastState.id,
            target: step.id,
            animated: isActive,
            style: {
              stroke: isCompleted ? '#10b981' : isActive ? '#3253DC' : '#d1d5db',
              strokeWidth: 2,
            },
          });
        } else {
          // Connect previous procedural step to current
          flowEdges.push({
            id: `e-${proceduralSteps[index - 1].id}-${step.id}`,
            source: proceduralSteps[index - 1].id,
            target: step.id,
            animated: isActive,
            style: {
              stroke: isCompleted ? '#10b981' : isActive ? '#3253DC' : '#d1d5db',
              strokeWidth: 2,
            },
          });
        }
      });
    } else {
      // Fallback: if states array is empty, show minimal flow
      flowNodes.push({
        id: stateMachine.currentState || 'unknown',
        type: 'default',
        position: { x: 200, y: 100 },
        data: {
          label: (
            <div className="flex flex-col items-center gap-2">
              <div className="w-12 h-12 rounded-full bg-blue-600 flex items-center justify-center">
                <Loader2 className="w-6 h-6 text-white animate-spin" />
              </div>
              <span className="text-sm font-medium text-center max-w-[120px]">
                {stateMachine.currentState || 'Unknown'}
              </span>
            </div>
          ),
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      });
      
      flowEdges.push({
        id: 'e-user-request-fallback',
        source: 'user-request',
        target: stateMachine.currentState || 'unknown',
        style: {
          stroke: '#3253DC',
          strokeWidth: 2,
        },
        animated: true,
      });
      
      // Legacy parallel paths visualization (should not be reached with new structure)
      // Commented out - using linear states structure now
      /*
      const pathCount = stateMachine.parallelPaths?.length || 0;
      const pathHeight = 150;
      const startY = 50;
      const userRequestY = startY + (pathCount - 1) * pathHeight / 2;

      // Update user request position for parallel paths
      userRequestNode.position = { x: 0, y: userRequestY };

      stateMachine.parallelPaths.forEach((path, pathIndex) => {
        const y = startY + pathIndex * pathHeight;
        
        // States in this path
        path.states.forEach((state, stateIndex) => {
          const isCompleted = state.status === 'completed';
          const isActive = state.status === 'active';
          const isPending = state.status === 'pending';
          
          flowNodes.push({
            id: `path-${path.id}-${state.id}`,
            type: 'default',
            position: { x: 200 + stateIndex * 200, y: y },
            data: {
              label: (
                <div className="flex flex-col items-center gap-2">
                  <div 
                    className={`w-12 h-12 rounded-full flex items-center justify-center ${
                      isCompleted ? 'bg-green-500' : isActive ? 'bg-blue-600 animate-pulse' : 'bg-gray-300'
                    }`}
                    style={isActive ? { backgroundColor: '#2563eb' } : undefined}
                  >
                    {isCompleted ? (
                      <CheckCircle2 className="w-6 h-6 text-white" />
                    ) : isActive ? (
                      <Loader2 className="w-6 h-6 text-white animate-spin" />
                    ) : (
                      <Circle className="w-6 h-6 text-white" />
                    )}
                  </div>
                  <span className={`text-sm font-medium text-center max-w-[120px] ${
                    isPending ? 'text-gray-400' : ''
                  }`}>
                    {state.name}
                  </span>
                </div>
              ),
            },
            sourcePosition: Position.Right,
            targetPosition: Position.Left,
          });

          if (stateIndex === 0) {
            // Connect user request to first state of each path
            flowEdges.push({
              id: `e-user-request-${path.id}`,
              source: 'user-request',
              target: `path-${path.id}-${state.id}`,
              animated: isActive,
              style: {
                stroke: isCompleted ? '#10b981' : isActive ? '#3253DC' : '#d1d5db',
                strokeWidth: 2,
              },
            });
          } else {
            // Connect previous state to current state
            flowEdges.push({
              id: `e-${path.id}-${stateIndex - 1}-${stateIndex}`,
              source: `path-${path.id}-${path.states[stateIndex - 1].id}`,
              target: `path-${path.id}-${state.id}`,
              animated: isActive,
              style: {
                stroke: isCompleted ? '#10b981' : isActive ? '#3253DC' : '#d1d5db',
                strokeWidth: 2,
              },
            });
          }
        });
      });

      // Find the maximum number of states in any path
      const maxStates = Math.max(...stateMachine.parallelPaths.map(p => p.states.length));
      const convergenceX = 200 + maxStates * 200;
      const convergenceY = startY + (pathCount - 1) * pathHeight / 2;

      // Check if all required paths are complete
      const allRequiredPathsComplete = stateMachine.parallelPaths
        .filter(path => path.required)
        .every(path => path.states.every(state => state.status === 'completed'));

      // Always show convergence point (even if not all paths complete)
      const convergenceCompleted = allRequiredPathsComplete;
      // Convergence point should only be active if it's actually completed (milestone reached)
      // It shouldn't be active just because dependent paths are active
      const convergenceActive = false;

      flowNodes.push({
        id: 'convergence',
        type: 'default',
        position: { x: convergenceX + 100, y: convergenceY },
        data: {
          label: (
            <div className="flex flex-col items-center gap-2">
              <div 
                className={`w-12 h-12 rounded-full flex items-center justify-center ${
                  convergenceCompleted ? 'bg-green-500' : convergenceActive ? 'bg-primary animate-pulse' : 'bg-gray-300'
                }`}
              >
                {convergenceCompleted ? (
                  <CheckCircle2 className="w-6 h-6 text-white" />
                ) : convergenceActive ? (
                  <Loader2 className="w-6 h-6 text-white animate-spin" />
                ) : (
                  <Circle className="w-6 h-6 text-white" />
                )}
              </div>
              <span className={`text-sm font-medium ${
                !convergenceCompleted && !convergenceActive ? 'text-gray-400' : ''
              }`}>
                Ready to Provision
              </span>
            </div>
          ),
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      });

      // Connect last state of each path to convergence
      stateMachine.parallelPaths.forEach((path) => {
        const lastState = path.states[path.states.length - 1];
        const isPathComplete = path.states.every(state => state.status === 'completed');
        flowEdges.push({
          id: `e-${path.id}-convergence`,
          source: `path-${path.id}-${lastState.id}`,
          target: 'convergence',
          style: {
            stroke: isPathComplete ? '#10b981' : '#d1d5db',
            strokeWidth: 2,
          },
        });
      });

      // Add procedural steps after convergence
      const terraformStatus = convergenceCompleted && requestStatus === 'provisioning' ? 'active' : 
                              requestStatus === 'completed' ? 'completed' : 'pending';
      const finishedStatus = requestStatus === 'completed' ? 'completed' : 'pending';
      
      const proceduralSteps = [
        { id: 'terraform-apply', name: 'Terraform Apply', status: terraformStatus },
        { id: 'finished', name: 'Finished', status: finishedStatus },
      ];

      proceduralSteps.forEach((step, index) => {
        const stepX = convergenceX + 200 + (index + 1) * 200;
        const isCompleted = step.status === 'completed';
        const isActive = step.status === 'active';
        const isPending = step.status === 'pending';

        flowNodes.push({
          id: step.id,
          type: 'default',
          position: { x: stepX, y: convergenceY },
          data: {
            label: (
              <div className="flex flex-col items-center gap-2">
                <div 
                  className={`w-12 h-12 rounded-full flex items-center justify-center ${
                    isCompleted ? 'bg-green-500' : isActive ? 'bg-blue-600 animate-pulse' : 'bg-gray-300'
                  }`}
                >
                  {isCompleted ? (
                    <CheckCircle2 className="w-6 h-6 text-white" />
                  ) : isActive ? (
                    <Loader2 className="w-6 h-6 text-white animate-spin" />
                  ) : (
                    <Circle className="w-6 h-6 text-white" />
                  )}
                </div>
                <span className={`text-sm font-medium text-center max-w-[120px] ${
                  isPending ? 'text-gray-400' : ''
                }`}>
                  {step.name}
                </span>
              </div>
            ),
          },
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
        });

        if (index === 0) {
          // Connect convergence to first procedural step
          flowEdges.push({
            id: 'e-convergence-terraform',
            source: 'convergence',
            target: step.id,
            animated: isActive,
            style: {
              stroke: isCompleted ? '#10b981' : isActive ? '#3253DC' : '#d1d5db',
              strokeWidth: 2,
            },
          });
        } else {
          // Connect previous procedural step to current
          flowEdges.push({
            id: `e-${proceduralSteps[index - 1].id}-${step.id}`,
            source: proceduralSteps[index - 1].id,
            target: step.id,
            animated: isActive,
            style: {
              stroke: isCompleted ? '#10b981' : isActive ? '#3253DC' : '#d1d5db',
              strokeWidth: 2,
            },
          });
        }
      });
      */
    }

    return { nodes: flowNodes, edges: flowEdges };
  }, [stateMachine, requestStatus]);

  return (
    <div className="w-full h-[500px] border border-gray-200 rounded-lg bg-white overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        className="react-flow-container"
      >
        <Background />
        <Controls />
        <MiniMap />
      </ReactFlow>
    </div>
  );
}

