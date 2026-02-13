"""
Report Execution State Machine.
Executes scheduled reports by running agent prompts and sending emails.
"""
from typing import List, Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import add_fact, has_fact, get_latest_fact
from app.agents.runner import AgentRunner
from app.tools import get_read_only_tools
from app.providers.notifications.client import NotificationProvider
from app.models.request import RequestStatus
import logging
import json

logger = logging.getLogger(__name__)

class ReportExecutionStateMachine(BaseRequestStateMachine):
    """
    Executes a scheduled report:
    1. EXECUTE_PROMPTS: Runs agent for each prompt associated with the subscription.
    2. ASSEMBLE_REPORT: Formats the results into an HTML email.
    3. DISTRIBUTE: Sends the email to subscribers.
    """
    
    # States
    pending = State("pending", initial=True)
    execute_prompts = State("execute_prompts")
    assemble_report = State("assemble_report")
    distribute = State("distribute")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True) # Standard fallback

    # Transitions
    submit = pending.to(execute_prompts, cond="has_request_submitted")
    prompts_done = execute_prompts.to(assemble_report, cond="has_prompts_executed")
    assembly_done = assemble_report.to(distribute, cond="has_report_assembled")
    distribute_done = distribute.to(completed, cond="has_distribution_completed")
    
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        execute_prompts.to(rejected, cond="has_request_rejected")
    )

    # Configuration
    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "execute_prompts": "prompts_executed",
        "assemble_report": "report_assembled",
        "distribute": "distribution_completed",
        "rejected": "request_rejected"
    }

    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "execute_prompts": ["prompts_executed"],
        "assemble_report": ["report_assembled"],
        "distribute": ["distribution_completed"],
        "rejected": ["request_rejected"]
    }

    STATUS_MAPPING = {
        "pending": RequestStatus.PENDING,
        "execute_prompts": RequestStatus.PROVISIONING,
        "assemble_report": RequestStatus.PROVISIONING,
        "distribute": RequestStatus.PROVISIONING,
        "completed": RequestStatus.COMPLETED,
        "rejected": RequestStatus.REJECTED
    }

    


    # --------------------------------------------------------------------------
    # Facts & Properties
    # --------------------------------------------------------------------------
    
    @property
    def has_prompts_executed(self) -> bool:
        return has_fact(self.db, self.request.id, "prompts_executed")

    @property
    def has_report_assembled(self) -> bool:
        return has_fact(self.db, self.request.id, "report_assembled")

    @property
    def has_distribution_completed(self) -> bool:
        return has_fact(self.db, self.request.id, "distribution_completed")

    # --------------------------------------------------------------------------
    # Async Actions
    # --------------------------------------------------------------------------

    async def on_enter_execute_prompts_async(self):
        """Runs the agent for each configured prompt using tools to avoid hallucinations."""
        if self.has_prompts_executed:
            return

        try:
            logger.info(f"[{self.request.id}] Executing report prompts with tools...")
            prompts = self.request.state_context.get("prompts", [])
            results = []
            
            # Use read-only tools for reports
            tools = get_read_only_tools()
            
            # System prompt for reporting
            system_prompt = (
                "You are a specialized read-only reporting assistant. "
                "Your goal is to fetch real data using your tools and present it clearly. "
                "Always return the final result as a clean HTML snippet (e.g. <table>, <ul>, <p>). "
                "Do not include <html> or <body> tags. "
                "If you cannot find data, state that clearly instead of making it up. "
                f"The current time is {datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d %H:%M:%S %Z')}."
            )
            
            runner = AgentRunner(system_prompt=system_prompt, tools=tools)
            
            for p in prompts:
                label = p.get("label", "Untitled")
                prompt_text = p.get("prompt", "")
                
                logger.info(f"[{self.request.id}] Running report prompt: {label}")
                
                # Execute agent with tools
                response = await runner.run(query=prompt_text)
                content = response.get("content", "")
                
                # Simple cleanup of markdown blocks if present
                content = content.replace("```html", "").replace("```", "").strip()
                
                results.append({
                    "label": label,
                    "html": content
                })
            
            # Store results in state context AND as a fact
            updated_context = (self.request.state_context or {}).copy()
            updated_context["report_results"] = results
            self.request.state_context = updated_context
            self.db.add(self.request) # Ensure context update is staged
            
            add_fact(self.db, self.request.id, "prompts_executed", {"count": len(results)}, actor="system")
            self.db.commit()
            
        except Exception as e:
            logger.error(f"[{self.request.id}] Failed to execute prompts: {e}", exc_info=True)
            raise e

    async def on_enter_assemble_report_async(self):
        """Assembles the final HTML email body."""
        if self.has_report_assembled:
            return
            
        try:
            results = self.request.state_context.get("report_results", [])
            report_name = self.request.state_context.get("name", "Report")
            
            # Generate HTML Fragment (not full document)
            html_body = f"""
                <div class="report-header">
                    <h2 style="margin-top: 0;">{report_name}</h2>
                    <p style="color: #666; font-size: 0.9rem;">Generated at: {datetime.now(ZoneInfo("America/Los_Angeles")).strftime('%Y-%m-%d %H:%M:%S %Z')}</p>
                </div>
                <hr style="border: 0; border-top: 1px solid #eee; margin: 1.5rem 0;" />
            """
            
            for res in results:
                html_body += f"""
                <div class="report-section" style="margin-bottom: 2rem;">
                    <h3 style="color: #444; margin-bottom: 0.5rem;">{res['label']}</h3>
                    <div class="section-content">
                        {res['html']}
                    </div>
                </div>
                """
            
            # Store results in state context AND as a fact
            updated_context = (self.request.state_context or {}).copy()
            updated_context["final_report_html"] = html_body
            self.request.state_context = updated_context
            self.db.add(self.request)
            
            add_fact(self.db, self.request.id, "report_assembled", {}, actor="system")
            self.db.commit()
            
        except Exception as e:
            logger.error(f"[{self.request.id}] Failed to assemble report: {e}", exc_info=True)
            raise e

    async def on_enter_distribute_async(self):
        """Sends the report to subscribers."""
        if self.has_distribution_completed:
            return
            
        try:
             subscribers = self.request.state_context.get("subscribers", "")
             subject = f"Report: {self.request.state_context.get('name', 'Automated Report')}"
             body = self.request.state_context.get("final_report_html", "")
             
             provider = NotificationProvider()
             
             # Split subscribers by comma
             email_list = [e.strip() for e in subscribers.split(",") if e.strip()]
             
             for email in email_list:
                 await provider.send_email(
                     to=email,
                     subject=subject,
                     body=body,
                     is_html=True
                 )
                 
             add_fact(self.db, self.request.id, "distribution_completed", {"failed_count": 0, "sent_count": len(email_list)}, actor="system")
             self.db.commit()
             
        except Exception as e:
            logger.error(f"[{self.request.id}] Failed to distribute report: {e}", exc_info=True)
            raise e
