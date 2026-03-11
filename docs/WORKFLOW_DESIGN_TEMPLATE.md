# 📋 Self-Service Workflow Governance Template

## Instructions for the Governance Team: 
Use this document to define the business rules, approval chains, and required information for a new self-service workflow.

## Step 1: Workflow Overview
- Workflow Name: (e.g., Workspace Provisioning, Request Data Access)
- Primary Goal: (Briefly describe what the user is trying to accomplish)

## Step 2: Agent Instructions
The AI Agent acts as the first line of defense. It gathers information through a chat conversation before any actual request is submitted. The agent can collect information, run tools to validate information, and make decisions based on the information it collects. 

1. Required Information (The "Form") 
- What exact pieces of information MUST the Agent collect from the user?
- (Example: Project Name, Cost Center, Data Classification Level)

2. Intent & "Pushback" Guardrails
- What questions should the Agent ask to ensure the user actually needs this and isn't over-requesting?
- (Example: "Why do you need Admin access instead of Read-Only?")

3. Alternatives
- Are there scenarios where the Agent should suggest a cheaper or safer alternative?
- (Example: "If they ask for a large cluster, suggest they use serverless first.")

4. Tool Calls
- What should the agent check before moving forward? 
- (Example: Check if the user already has access to the data they are requesting. Check if the user already has a workspace that they can use. Check if the user already has a cluster that they can use.)

## Step 3: State Machine Execution (Approvals & Deterministic Steps)
Once the Agent gathers the requirements, the State Machine takes over to enforce deterministic business rules, route approvals, and provision the assets.

### State Machine Decisions
1. Risk-Based Approvals
- State machines categorize requests by risk level to determine if the workflow should pause for human-in-the-loop sign-off.
- Low-Risk (Automated) Scenario: Can this request ever be fully automated with no human approval? If yes, what groups or other criteria must be met for this to be true?
- High-Risk (Manual) Scenario: Who needs to explicitly approve this request if it is deemed high-risk? (e.g., Direct Manager Approval -> Data Owner Approval)

2. Mandatory Training
- Does the user need to complete specific training before this request provisions?

3. Mandatory Tagging
- What tags are strictly required to be applied to the newly provisioned resources (e.g., CostCenter, Project, Owner)?

4. Notification
- Who should be notified? N/A is an acceptable answer.
- Success: (e.g., email the user, notify admins via Teams)
- Failure: (e.g., email the user, notify admins via Teams)
- On Request: (e.g., email the user, notify admins via Teams)

### State Machine Steps
Put all of the pieces together in a step by step format. List all steps, and what has to happen to go from on to the other
Example: 
- "Requested" -> (request was made by user) -> "Manager Approval" 
- "Manager Approval"-> (manager approves or denies) -> "Data Owner Approval" 
- "Data Owner Approval" -> (data owner approves or denies) -> "Provisioning" 
- "Provisioning" -> (resources are provisioned) -> "Notification"
- "Notification" -> (user is notified of completion) -> "Complete"

## Step 4: Reactive Enforcers & Lifecycles
This layer defines how the platform continuously audits and prunes the requested assets over time to ensure policy adherence and prevent resource sprawl.

- Time-Bound Access: Does this asset or access expire automatically?
- Use it or lose it: Does this asset or access need to be periodically audited for inactivity?