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
            <div className="w-12 h-12 rounded-full bg-primary flex items-center justify-center">
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

    if (stateMachine.parallelPaths.length === 0) {
      // Simple linear flow - connect user request to first state
      const states = ['pending', ...stateMachine.activeStates, ...stateMachine.completedStates];
      const allStatesComplete = states.every(state => stateMachine.completedStates.includes(state));
      
      states.forEach((state, index) => {
        const isCompleted = stateMachine.completedStates.includes(state);
        const isActive = stateMachine.activeStates.includes(state);
        const isPending = !isCompleted && !isActive;
        
        flowNodes.push({
          id: state,
          type: 'default',
          position: { x: 200 + index * 200, y: 100 },
          data: {
            label: (
              <div className="flex flex-col items-center gap-2">
                <div 
                  className={`w-12 h-12 rounded-full flex items-center justify-center ${
                    isCompleted ? 'bg-green-500' : isActive ? 'bg-primary animate-pulse' : 'bg-gray-300'
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
                  {state.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
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
            target: state,
            style: {
              stroke: isCompleted ? '#10b981' : isActive ? '#3253DC' : '#d1d5db',
              strokeWidth: 2,
            },
            animated: isActive,
          });
        } else {
          flowEdges.push({
            id: `e${index - 1}-${index}`,
            source: states[index - 1],
            target: state,
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
                    isCompleted ? 'bg-green-500' : isActive ? 'bg-primary animate-pulse' : 'bg-gray-300'
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
          flowEdges.push({
            id: `e-${states[states.length - 1]}-terraform`,
            source: states[states.length - 1],
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
      // Parallel paths visualization
      const pathCount = stateMachine.parallelPaths.length;
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
                      isCompleted ? 'bg-green-500' : isActive ? 'bg-primary animate-pulse' : 'bg-gray-300'
                    }`}
                    style={isActive ? { backgroundColor: '#3253DC' } : undefined}
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
      const convergenceActive = !convergenceCompleted && stateMachine.parallelPaths.some(path =>
        path.states.some(state => state.status === 'active')
      );

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
                    isCompleted ? 'bg-green-500' : isActive ? 'bg-primary animate-pulse' : 'bg-gray-300'
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
    }

    return { nodes: flowNodes, edges: flowEdges };
  }, [stateMachine]);

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

