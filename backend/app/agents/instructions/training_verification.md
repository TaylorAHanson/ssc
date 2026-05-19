# Training Verification

**Goal**: Verify that a user has completed a required training course.

## Context
This workflow allows users (or managers) to request verification of training completion, which is often a prerequisite for elevated access.

## Information to Gather
1. **User Email**: The email of the person who completed the training.
    *   *Default*: Use the current user's email if they are verifying their own training.
2. **Course ID or Name**: The identifier or name of the course they completed.

## Pre-Check (REQUIRED)
Before calling `execute_workflow`, you MUST use the `check_training_status` tool to see if the system already recognizes that the user has completed this course. 
* If the tool shows they have completed it, inform the user that their training is already verified and no further action is needed!
* If the tool does NOT show completion, proceed with the manual verification workflow below.

## Execution
Once all information is confirmed, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "training_verification",
  "parameters": {
    "user_email": "...",
    "course_id": "..."
  }
}
```
