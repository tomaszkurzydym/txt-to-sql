# Email to Raul

**To:** Raul Jimenez <raul.j@gitlab.com>  
**From:** Solutions Team <solutions@fireworks.ai>  
**Subject:** Re: Help Needed: Agentic BI CLI Product for GitLab Customers

---

Hi Raul,

Thanks for the detailed requirements brief. I built the proof-of-concept that addresses the quality, latency, and cost concerns you raised, against the sample database using open models hosted on our platform. Please see my short write up below.

## Assumptions

I made a few assumptions:

1. **Which engines?** I assumed SQLite only, since that's the sample database. In production, how many engines do your first customers actually bring, and in what order?  
     
2. **How wide are customer schemas?** I assumed schemas the size of sample tables (less then 2k tokens) and in my PoC the whole schema card goes in the prompt and nothing is retrieved - at this scale, pruning can only lose information. However above ~15K tokens of schema, retrieval becomes necessary.

## What I built

An interactive terminal agent that:

* Grounds every request in your live SQLite schema plus the BIRD column documentation (including value enums like loan status A/B/C/D, OWNER vs DISPONENT, and district regions). For your database that's less than 2k tokens — small enough to sit in the system prompt as a cacheable prefix, so no retrieval layer is needed at this scale.   
* Generates read-only SQL via Fireworks (configurable; I benchmarked kimi-k2p6 and deepseek-v4p1-flash)  
* Executes the query, shows SQL + results (handles large output) and supports follow-up questions  
* Retries automatically (1–2 times) when SQLite returns an error, feeding the error back to the model  
* Rejects non-SELECT / dangerous statements before execution

In this design, schema grounding and a repair loop are the main levers that reduce hallucinated tables/columns and invalid SQL and improved accuracy. 

## Key findings

* **Quality**: Schema docs + explicit enum hints (region names, loan status letters, OWNER vs DISPONENT) were enough to reach 10/10 on the public dev set, including tier-3 multi-join questions. Remaining production risk is generalization beyond these 10 questions.  
* **Latency**: I instrumented end-to-end wall time (full LLM round-trip + SQLite). On the same 10 questions:  
  * Quality-first kimi-k2p6: P50 ≈ 6.1s, P95 ≈ 54s   
  * Latency-oriented deepseek-v4p1-flash: P50 ≈ 0.85s, P95 ≈ 2.5s, 10/10 result-match, all 10 under 3s. This clears the <3s P50 target  
  * *Recommendation*: implement a router in production (flash/fast default, escalate harder queries to a stronger model) rather than one model for every ask.  
* **Cost**: Your prototype used GPT-5.5 (~7s). Illustrative 30,000 queries/day using this PoC’s measured prompt (~2,250 input tokens: schema + question) and ~50 SQL output tokens:

| Model | Uncached $/query | Uncached $/day | Prompt-cached $/day |
| ----- | ----: | ----: | ----: |
| GPT-5.5 | ~$0.013 | ~$379 | ~$82 |
| Kimi K2.6 | ~$0.0023 | ~$69 (~5.5× cheaper) | ~$17 |
| DeepSeek V4 Flash | ~$0.0005 | ~$16 (~24× cheaper) | ~$1.60 |

So in a nutshell, providing the meaningful context to the model makes the response more accurate and limits hallucinations; using open models make this solution economically viable. DeepSeek Flash is the cost *and* latency default; we can use kimi (or similar) as an escalation tier.

## What we should address next

1. Schema retrieval for larger customer DBs  
2. Eval harness in CI — expand beyond the 10 public questions; track result-match rate, p50/p95 latency, $/query so every change is measured  
3. Safety & tenancy — read-only DB roles, statement timeouts, row limits, and per-customer connection strings as you envisioned.  
4. Latency budget — we now measure P50/P95 (LLM vs SQL); next is streaming TTFT, model routing in the CLI  
5. Second engine - whichever dialect your customers actually need.  
   

Post-training comes after that, and only once the eval set exists.

## Post-training (fine-tuning) recommendation  

Prompt and retrieval should remain the first line of defense. Post-training becomes worth it once failure modes are stable and repeated, and once there's an eval set that can prove a change helped. The data doesn't need hand-written gold SQL: correctness here is verifiable by execution, so the CLI's own logs, every question, query, error, repair and user correction, filter into training pairs automatically. I'd sequence it as a Fireworks LoRA to lock in dialect, output format and repair behaviour, then reinforcement fine-tuning with an execution reward, which is where the accuracy is. Two caveats, I'd tune the schema-independent skill rather than this database's join patterns, since your customers bring their own schemas; and tuned models need dedicated capacity, so at the initial 30K queries/day this buys accuracy and latency control rather than savings. 

I'm happy to walk you through the design and the code, work through the questions above, and plan the next phase. Please send over a time that suits you next week and I will send out an invite.

Best regards,   
Tomasz Kurzydym

AI Engineering Lead, Fireworks AI
