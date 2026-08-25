import { SystemNode, SimulationPreset, CameraViewPreset } from './types';

export const SYSTEM_NODES: SystemNode[] = [
  {
    id: 'pii_redaction',
    stepNumber: 1,
    title: 'Safety & Data-Minimisation Gate',
    subtitle: 'Redaction, policy checks & audit signals',
    tagline: 'Reduces sensitive-data exposure before durable memory processing',
    iconName: 'ShieldAlert',
    color: '#00f0ff', // Cyan
    glowColor: 'rgba(0, 240, 255, 0.4)',
    position: [-18, 2, -4],
    geometryType: 'hexagon_shield',
    storeTarget: 'security',
    purpose: 'Applies configurable redaction and policy checks before data becomes durable memory. It is a defence-in-depth control, not a substitute for an organisation’s privacy programme or legal compliance review.',
    deepExplanation: [
      'Runs deterministic secret and personally identifying data detectors before persistent memory writes.',
      'Keeps the durable-memory path separate from transient conversation state so storage policy can be enforced at the boundary.',
      'Emits metadata-only audit signals for redaction events without storing cleartext replacements in the audit trail.',
      'Flags credentials and sensitive identifiers for review or blocking according to the project policy.'
    ],
    codeSnippets: [
      {
        filename: 'pipeline.py',
        filepath: 'backend/app/memory/pipeline.py',
        language: 'python',
        description: 'PII Redaction entry point in process_message_pair()',
        code: `cleaned_user, redactions = redact(user_message)
if redactions:
    audit.pii_redacted(user_id, [r.label for r in redactions])
    logger.info("PII redacted for user=%s: %s", user_id, [r.label for r in redactions])
    user_message = cleaned_user`
      },
      {
        filename: 'pii.py',
        filepath: 'backend/app/safety/pii.py',
        language: 'python',
        description: 'Redaction regex engine and replacement logic',
        code: `def redact(text: str) -> tuple[str, list[Redaction]]:
    redactions = []
    # Pattern matching for SSNs, Credit Cards, Emails, Phone Numbers
    for label, pattern in PII_PATTERNS.items():
        matches = pattern.finditer(text)
        for m in matches:
            redactions.append(Redaction(label=label, start=m.start(), end=m.end()))
            text = text.replace(m.group(0), f"[{label.upper()}_REDACTED]")
    return text, redactions`
      }
    ],
    mathFormulas: [
      {
        title: 'PII Sanitization Function',
        latex: 'f_{\\text{sanitize}}(T) = T \\setminus \\bigcup_{i} \\text{Match}(P_i, T) \\cup \\{\\text{TOKEN}_i\\}',
        explanation: 'Maps input text T to a sanitized version where matching PII pattern substrings are atomically replaced by privacy tokens.'
      }
    ],
    dataTransformation: {
      inputLabel: 'Raw Ingested User Message',
      inputJson: {
        raw_message: 'Hi, my SSN is 123-45-6789 and my secret key is sk_live_9948123. I moved to Tokyo today.',
        user_id: 'acct_98410a',
        timestamp: '2026-08-04T17:45:00Z'
      },
      outputLabel: 'Sanitized Output Message',
      outputJson: {
        cleaned_message: 'Hi, my SSN is [SSN_REDACTED] and my secret key is [API_KEY_REDACTED]. I moved to Tokyo today.',
        redactions_applied: ['SSN', 'API_KEY'],
        audit_logged: true
      }
    },
    keyMetrics: [
      { label: 'Execution', value: 'Pre-write policy gate' },
      { label: 'Evidence', value: 'Metadata-only audit event' },
      { label: 'Mode', value: 'Project configurable' }
    ]
  },
  {
    id: 'redis_state',
    stepNumber: 2,
    title: 'Redis Conversation State',
    subtitle: 'Transient context, summaries & live events',
    tagline: 'Maintains rolling conversation history and generates periodic thread summaries',
    iconName: 'Database',
    color: '#ff0055', // Red/Pink
    glowColor: 'rgba(255, 0, 85, 0.4)',
    position: [-12, 0, -2],
    geometryType: 'pulsing_core',
    storeTarget: 'redis',
    purpose: 'Maintains short-lived conversation state, summaries, and streaming coordination. Durable facts are written asynchronously to the memory pipeline so user responses are not held hostage by ingestion.',
    deepExplanation: [
      'Chat turns and SSE coordination stay in Redis, scoped by account and conversation.',
      'A rolling summary is refreshed on a bounded cadence to control prompt size.',
      'The summary is retrieval evidence, not a replacement for original memory provenance.',
      'Background work can retry independently without delaying the next user-visible response.'
    ],
    codeSnippets: [
      {
        filename: 'pipeline.py',
        filepath: 'backend/app/memory/pipeline.py',
        language: 'python',
        description: 'Redis state append & periodic summary trigger',
        code: `await state.append_message(redis, conversation_id, Message(role="user", content=user_message), user_id=user_id)
await state.append_message(redis, conversation_id, Message(role="assistant", content=assistant_message), user_id=user_id)
history = await state.get_history(redis, conversation_id, limit=50, user_id=user_id)

turn_count = await state.incr_turn_counter(redis, conversation_id, user_id=user_id)
if turn_count % SUMMARY_REFRESH_INTERVAL == 0:
    refreshed_summary = await refresh_summary(redis, conversation_id, history, user_id=user_id)`
      }
    ],
    mathFormulas: [
      {
        title: 'Summary Refresh Cadence',
        latex: 'N_{\\text{refresh}} = \\mathbb{I}(N_{\\text{turn}} \\pmod{10} = 0)',
        explanation: 'Fires summary compression every 10 turns (20 appended turns), reducing Groq API token burn by 50%.'
      }
    ],
    dataTransformation: {
      inputLabel: 'Appended Turn Payload',
      inputJson: {
        conversation_id: 'conv_8812',
        turn_count: 10,
        added_role: 'user',
        history_length: 10
      },
      outputLabel: 'Updated Redis State & Summary',
      outputJson: {
        redis_key: 'chat:conv_8812:summary',
        rolling_summary: 'User lives in Tokyo, works as a senior software engineer at Sony, and prefers dark mode UI.',
        ttl_seconds: 604800 // 7 days
      }
    },
    keyMetrics: [
      { label: 'Storage Engine', value: 'Redis In-Memory' },
      { label: 'TTL', value: '7 Days' },
      { label: 'Summary Window', value: 'Every 10 Turns' }
    ]
  },
  {
    id: 'fact_extraction',
    stepNumber: 3,
    title: 'Structured Memory Extraction',
    subtitle: 'User-grounded facts, entities & temporal signals',
    tagline: 'Converts eligible user statements into evidence-linked candidate memories',
    iconName: 'Cpu',
    color: '#39ff14', // Neon Green
    glowColor: 'rgba(57, 255, 20, 0.4)',
    position: [-5, 3, 0],
    geometryType: 'neural_sphere',
    storeTarget: 'llm',
    purpose: 'Uses the configured non-thinking extraction provider (Sarvam with Gemini fallback) to create candidate memories from user-grounded evidence. Candidates carry confidence, provenance, and temporal information.',
    deepExplanation: [
      'Reads ONLY user-derived text (User message + user history window + thread summary). Assistant content is strictly excluded from prompt to prevent tool echo bugs.',
      'Uses Llama 3.1 8B Instant on Groq for sub-100ms structured extraction.',
      'Outputs candidate PendingMemory objects containing candidate text, categories (identity, preference, event, work), temporal valid_at dates, and entity hints.',
      'Ignores generic small talk while keeping candidate facts for downstream deduplication.'
    ],
    codeSnippets: [
      {
        filename: 'extractor.py',
        filepath: 'backend/app/memory/extractor.py',
        language: 'python',
        description: 'LLM Prompting & Tool Leak Protection',
        code: `candidates = await extract_memories(
    user_message, assistant_message, context_history, summary, user_id, conversation_id
)
# Returns list of PendingMemory objects:
# [PendingMemory(text="User moved to Tokyo on Aug 4, 2026", confidence=0.96)]`
      }
    ],
    mathFormulas: [
      {
        title: 'Fact Extraction Probability',
        latex: 'P(\\text{Fact} \\mid U, H_{\\text{user}}, S) = \\sigma(W^T \\cdot \\text{LLM}_{8B}(U \\oplus H_{\\text{user}} \\oplus S))',
        explanation: 'Evaluates structured candidate facts from user context window U, history H, and summary S.'
      }
    ],
    dataTransformation: {
      inputLabel: 'Extractor Context Input',
      inputJson: {
        user_message: 'I just moved to Tokyo today and started working as a senior engineer at Sony!',
        context_summary: 'User previously lived in Seattle.'
      },
      outputLabel: 'Extracted PendingMemories',
      outputJson: {
        candidates: [
          {
            text: 'User moved to Tokyo on August 4, 2026',
            category: 'location',
            confidence: 0.98,
            entities: ['User', 'Tokyo'],
            source: 'USER_DIRECT'
          },
          {
            text: 'User works as a Senior Software Engineer at Sony',
            category: 'career',
            confidence: 0.95,
            entities: ['User', 'Senior Software Engineer', 'Sony'],
            source: 'USER_DIRECT'
          }
        ]
      }
    },
    keyMetrics: [
      { label: 'Provider chain', value: 'Sarvam → Gemini fallback' },
      { label: 'Execution', value: 'Background & retryable' },
      { label: 'Grounding', value: 'User-source aware' }
    ]
  },
  {
    id: 'cosine_dedup',
    stepNumber: 4,
    title: 'Memory Reconciliation Gate',
    subtitle: 'Semantic similarity, provenance & contradiction checks',
    tagline: 'Decides whether a candidate is new, reinforcing, superseding, or unsafe to store',
    iconName: 'GitCompare',
    color: '#ffaa00', // Amber
    glowColor: 'rgba(255, 170, 0, 0.4)',
    position: [2, 0, -2],
    geometryType: 'twin_gate',
    storeTarget: 'qdrant',
    purpose: 'Compares a candidate against scoped prior evidence. Semantic similarity is one signal; entity overlap, source role, confidence, and temporal state prevent a single threshold from silently overwriting a real fact.',
    deepExplanation: [
      'Candidate facts are converted to dense vector embeddings using fast embedding models.',
      'Executes similarity search against existing Qdrant memory points for the specific user_id.',
      'Applies strict decision thresholding:',
      '  • Cosine Similarity >= 0.92: Exact or near-identical duplicate -> NOOP (discard).',
      '  • 0.70 <= Cosine Similarity < 0.91 or Entity conflict: UPDATE (supersede old fact).',
      '  • Cosine Similarity < 0.70: ADD (write new memory point).'
    ],
    codeSnippets: [
      {
        filename: 'updater.py',
        filepath: 'backend/app/memory/updater.py',
        language: 'python',
        description: 'Cosine similarity threshold classification',
        code: `async def classify_and_execute_batch(candidates, qdrant, neo4j=None):
    results = []
    for cand in candidates:
        similar = await search_similar_memories(qdrant, cand.text, cand.user_id)
        if similar and similar[0].score >= 0.92:
            results.append((Operation.NOOP, None)) # Duplicate
        elif similar and similar[0].score >= 0.70:
            # Conflict or refinement -> Update
            updated = await update_memory_point(qdrant, similar[0].id, cand)
            results.append((Operation.UPDATE, updated))
        else:
            new_mem = await add_memory_point(qdrant, cand)
            results.append((Operation.ADD, new_mem))
    return results`
      }
    ],
    mathFormulas: [
      {
        title: 'Cosine Similarity Threshold Gate',
        latex: '\\text{Sim}(\\vec{u}, \\vec{v}) = \\frac{\\vec{u} \\cdot \\vec{v}}{\\|\\vec{u}\\| \\|\\vec{v}\\|}',
        explanation: 'Computes directional angle between candidate vector u and existing memory vector v.'
      }
    ],
    dataTransformation: {
      inputLabel: 'Candidate Vector Comparison',
      inputJson: {
        candidate_text: 'User lives in Tokyo',
        top_qdrant_match: { text: 'User moved to Tokyo on Aug 4', score: 0.94 }
      },
      outputLabel: 'Classification Result',
      outputJson: {
        similarity_score: 0.94,
        decision: 'NOOP',
        reason: 'Score >= 0.92 threshold — fact already represented in Qdrant'
      }
    },
    keyMetrics: [
      { label: 'Scope', value: 'Account + project isolated' },
      { label: 'Decision', value: 'Multi-signal reconciliation' },
      { label: 'History', value: 'Supersession links retained' }
    ]
  },
  {
    id: 'qdrant_vault',
    stepNumber: 5,
    title: 'Qdrant Vector Storage Vault',
    subtitle: 'Durable semantic evidence with scoped payloads',
    tagline: 'Persists memory evidence, metadata, and vector representations for retrieval',
    iconName: 'Layers',
    color: '#bd00ff', // Electric Purple
    glowColor: 'rgba(189, 0, 255, 0.4)',
    position: [9, 2, -4],
    geometryType: 'matrix_vault',
    storeTarget: 'qdrant',
    purpose: 'Stores durable memory evidence in Qdrant with strict account and project filters. Vectors enable semantic recall while payload metadata preserves source, time, confidence, and supersession signals.',
    deepExplanation: [
      'Each memory point contains the active embedding representation plus a rich JSON payload.',
      'Payload metadata fields include: id, text, user_id, conversation_id, project_id, valid_at, memory_type, confidence, and decay_score.',
      'Uses HNSW indexing with filtered candidate search; end-to-end retrieval also includes planning, reranking, and context packing.',
      'Isolates user data using strict collection payload filtering (user_id = acct_xxx).'
    ],
    codeSnippets: [
      {
        filename: 'context_index.py',
        filepath: 'backend/app/db/context_index.py',
        language: 'python',
        description: 'Qdrant Point Struct & Payload Creation',
        code: `point = PointStruct(
    id=memory_id,
    vector=embedding_vector,
    payload={
        "text": memory.text,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "project_id": project_id,
        "valid_at": valid_at.isoformat(),
        "created_at": datetime.now().isoformat(),
        "decay_factor": 1.0,
        "confidence": memory.confidence
    }
)
await qdrant.upsert(collection_name="memories", points=[point])`
      }
    ],
    mathFormulas: [
      {
        title: 'HNSW Graph Distance Metric',
        latex: 'd(\\mathbf{x}, \\mathbf{y}) = 1 - \\frac{\\mathbf{x} \\cdot \\mathbf{y}}{\\|\\mathbf{x}\\|_2 \\|\\mathbf{y}\\|_2}',
        explanation: 'Used by Qdrant index builder to construct multi-layer proximity graphs for fast search.'
      }
    ],
    dataTransformation: {
      inputLabel: 'Memory Payload to Upsert',
      inputJson: {
        memory_id: 'mem_77218a',
        vector_dim: 1536,
        user_id: 'acct_98410a',
        text: 'User works as Senior Software Engineer at Sony'
      },
      outputLabel: 'Qdrant Index Confirmation',
      outputJson: {
        status: 'COMPLETED',
        collection: 'memories',
        points_upserted: 1,
        hnsw_indexed: true
      }
    },
    keyMetrics: [
      { label: 'Vector index', value: 'HNSW + payload filters' },
      { label: 'Embeddings', value: 'Active + shadow migration lanes' },
      { label: 'Evidence', value: 'Source & time metadata' }
    ]
  },
  {
    id: 'neo4j_graph',
    stepNumber: 6,
    title: 'Temporal Knowledge Graph',
    subtitle: 'Entity relations, time & provenance',
    tagline: 'Constructs entity-relationship graphs linking subjects, predicates, and objects',
    iconName: 'Network',
    color: '#00e5ff', // Deep Cyan
    glowColor: 'rgba(0, 229, 255, 0.4)',
    position: [15, 0, -2],
    geometryType: 'graph_cluster',
    storeTarget: 'neo4j',
    purpose: 'Builds a scoped temporal graph in Neo4j from evidence-linked entities and relations. It is used to guide retrieval and explain relationship paths; raw text evidence remains the authority for final answers.',
    deepExplanation: [
      'Extracted memory text is parsed into entity triples by app/graph/pipeline.py (build_from_memory).',
      'Example SPO Triple: (User) -[:WORKS_AT]-> (Company: Sony) with property role: "Senior Software Engineer".',
      'Executes Cypher MERGE queries to guarantee node uniqueness while establishing directed edge relationships.',
      'Graph edges store references to Qdrant memory IDs, bridging vector search and graph search.'
    ],
    codeSnippets: [
      {
        filename: 'pipeline.py',
        filepath: 'backend/app/graph/pipeline.py',
        language: 'python',
        description: 'Neo4j Cypher Entity Graph Commit',
        code: `delta = await build_from_memory(memory, neo4j, pending=pending)
# Executes Cypher:
# MERGE (u:Entity {name: "User", user_id: $user_id})
# MERGE (c:Entity {name: "Sony", type: "Company"})
# MERGE (u)-[r:WORKS_AT {memory_id: $mem_id}]->(c)`
      }
    ],
    mathFormulas: [
      {
        title: 'Graph Knowledge Structure',
        latex: 'G = (V, E, R), \\quad \\text{where } (s, p, o) \\in E \\subseteq V \\times R \\times V',
        explanation: 'Defines entity vertices V, relation types R, and directed triple edges E.'
      }
    ],
    dataTransformation: {
      inputLabel: 'Memory to Graph Triples',
      inputJson: {
        text: 'User moved to Tokyo and works at Sony',
        memory_id: 'mem_77218a'
      },
      outputLabel: 'Neo4j Graph Delta Created',
      outputJson: {
        nodes_added: [
          { label: 'Entity', name: 'User' },
          { label: 'Location', name: 'Tokyo' },
          { label: 'Company', name: 'Sony' }
        ],
        edges_added: [
          { source: 'User', relation: 'MOVED_TO', target: 'Tokyo' },
          { source: 'User', relation: 'WORKS_AT', target: 'Sony' }
        ]
      }
    },
    keyMetrics: [
      { label: 'Graph model', value: 'Temporal entity relations' },
      { label: 'Database', value: 'Neo4j Cypher' },
      { label: 'Use', value: 'Retrieval guidance & evidence paths' }
    ]
  },
  {
    id: 'fused_retrieval',
    stepNumber: 7,
    title: 'Evidence-Guided Retrieval',
    subtitle: 'Multi-scope search, reranking & context packing',
    tagline: 'Combines semantic, lexical, graph, temporal, and provenance signals before the answer model sees context',
    iconName: 'Zap',
    color: '#ffea00', // Glowing Gold
    glowColor: 'rgba(255, 234, 0, 0.4)',
    position: [21, 2, -4],
    geometryType: 'prism_engine',
    storeTarget: 'qdrant',
    purpose: 'Runs parallel scoped retrieval, graph-guided expansion, reranking, and bounded context packing. The system sends compact, attributable evidence to the answer layer instead of a large undifferentiated memory dump.',
    deepExplanation: [
      'When user asks a question in Memento ReAct loop, fused_search() queries all 3 memory stores concurrently.',
      'Vector Search (Qdrant) finds semantic concepts; BM25 finds exact name/code matches; Neo4j graph finds linked relational facts.',
      'Applies Exponential Time Decay formula to adjust relevance scores based on age and reinforcement frequency.',
      'Reranks combined results and formats top memories directly into the LLM context prompt.'
    ],
    codeSnippets: [
      {
        filename: 'fused.py',
        filepath: 'backend/app/retrieval/fused.py',
        language: 'python',
        description: 'Multi-stream retrieval fusion & decay scoring',
        code: `results = await fused_search(
    query="What is my job and where do I live?",
    user_id=user_id,
    qdrant=qdrant,
    neo4j=neo4j,
    limit=5
)
# Returns rank-fused memories scored with exponential decay`
      }
    ],
    mathFormulas: [
      {
        title: 'Memory Relevance & Decay Score',
        latex: 'S_{\\text{final}} = \\left( w_v S_{\\text{vec}} + w_b S_{\\text{bm25}} + w_g S_{\\text{graph}} \\right) \\cdot e^{-\\lambda (t_{\\text{now}} - t_{\\text{event}})}',
        explanation: 'Fuses hybrid similarity scores weighted with exponential temporal decay factor lambda.'
      }
    ],
    dataTransformation: {
      inputLabel: 'Retrieval Query Input',
      inputJson: {
        user_query: 'Where do I work and what city am I in?',
        user_id: 'acct_98410a'
      },
      outputLabel: 'Fused Context Memories Returned',
      outputJson: {
        retrieved_memories: [
          { text: 'User works as Senior Software Engineer at Sony', score: 0.96, source: 'Qdrant+Neo4j' },
          { text: 'User moved to Tokyo on August 4, 2026', score: 0.94, source: 'Qdrant' }
        ],
        decay_factors_applied: [0.99, 1.0]
      }
    },
    keyMetrics: [
      { label: 'Streams Fused', value: 'Vector + BM25 + Graph' },
      { label: 'Reranker', value: 'Reciprocal Rank Fusion' },
      { label: 'Total Search Time', value: '~12 ms' }
    ]
  }
];

export const SIMULATION_PRESETS: SimulationPreset[] = [
  {
    id: 'personal_fact',
    title: 'Identity & Fact Ingestion',
    description: 'User states identity facts: "I moved to Tokyo today and started working as a senior engineer at Sony!"',
    rawUserMessage: 'I moved to Tokyo today and started working as a senior engineer at Sony!',
    assistantReply: 'Congratulations on the move to Tokyo and the new role at Sony! That sounds like an exciting new chapter.',
    steps: {
      pii_redaction: {
        stageId: 'pii_redaction',
        status: 'completed',
        statusMessage: 'No sensitive PII tokens found. Clean text passed forward.',
        durationMs: 1
      },
      redis_state: {
        stageId: 'redis_state',
        status: 'completed',
        statusMessage: 'Appended turn pair to Redis history list. Increment turn count to 14.',
        durationMs: 4
      },
      fact_extraction: {
        stageId: 'fact_extraction',
        status: 'completed',
        statusMessage: 'Extracted 2 atomic facts: "User moved to Tokyo" and "User works as Senior Software Engineer at Sony".',
        durationMs: 82
      },
      cosine_dedup: {
        stageId: 'cosine_dedup',
        status: 'completed',
        statusMessage: 'Cosine similarity against existing vectors = 0.32 (<0.70 threshold). Classified as ADD.',
        durationMs: 12
      },
      qdrant_vault: {
        stageId: 'qdrant_vault',
        status: 'completed',
        statusMessage: 'Point mem_881a upserted into Qdrant memories collection with 1536-dim embedding.',
        durationMs: 15
      },
      neo4j_graph: {
        stageId: 'neo4j_graph',
        status: 'completed',
        statusMessage: 'Created nodes (User, Tokyo, Sony) and SPO relations (MOVED_TO, WORKS_AT) in Neo4j.',
        durationMs: 24
      },
      fused_retrieval: {
        stageId: 'fused_retrieval',
        status: 'completed',
        statusMessage: 'Facts ready for instant multi-stream retrieval on subsequent chat turns.',
        durationMs: 8
      }
    }
  },
  {
    id: 'pii_shield_test',
    title: 'PII Redaction & Security Masking',
    description: 'User enters sensitive data: "My SSN is 123-45-6789 and my confidential phone is +1-555-0199."',
    rawUserMessage: 'My SSN is 123-45-6789 and my confidential phone is +1-555-0199.',
    assistantReply: 'I have noted your security request. All sensitive tokens have been sanitized.',
    steps: {
      pii_redaction: {
        stageId: 'pii_redaction',
        status: 'completed',
        statusMessage: 'PII Shield triggered! Redacted [SSN_REDACTED] and [PHONE_REDACTED]. Sanitized text generated.',
        durationMs: 2
      },
      redis_state: {
        stageId: 'redis_state',
        status: 'completed',
        statusMessage: 'Sanitized text saved to Redis history list.',
        durationMs: 3
      },
      fact_extraction: {
        stageId: 'fact_extraction',
        status: 'completed',
        statusMessage: 'Extractor analyzed sanitized text. Zero durable facts found to store.',
        durationMs: 70
      },
      cosine_dedup: {
        stageId: 'cosine_dedup',
        status: 'bypassed',
        statusMessage: 'No candidates produced -> Deduplication bypassed.',
        durationMs: 0
      },
      qdrant_vault: {
        stageId: 'qdrant_vault',
        status: 'bypassed',
        statusMessage: 'No vector write performed -> Zero Qdrant pollution.',
        durationMs: 0
      },
      neo4j_graph: {
        stageId: 'neo4j_graph',
        status: 'bypassed',
        statusMessage: 'No graph nodes added.',
        durationMs: 0
      },
      fused_retrieval: {
        stageId: 'fused_retrieval',
        status: 'completed',
        statusMessage: 'Privacy guarantee verified. Sensitive details never touched persistent memory stores.',
        durationMs: 2
      }
    }
  },
  {
    id: 'duplicate_noop',
    title: 'Duplicate Memory NOOP Pass',
    description: 'User repeats known fact: "As I mentioned, I work at Sony as an engineer."',
    rawUserMessage: 'As I mentioned, I work at Sony as an engineer.',
    assistantReply: 'Got it! I remember you work at Sony as an engineer.',
    steps: {
      pii_redaction: {
        stageId: 'pii_redaction',
        status: 'completed',
        statusMessage: 'No PII tokens found.',
        durationMs: 1
      },
      redis_state: {
        stageId: 'redis_state',
        status: 'completed',
        statusMessage: 'Turn pair appended to Redis history.',
        durationMs: 3
      },
      fact_extraction: {
        stageId: 'fact_extraction',
        status: 'completed',
        statusMessage: 'Extracted candidate: "User works at Sony as an engineer".',
        durationMs: 75
      },
      cosine_dedup: {
        stageId: 'cosine_dedup',
        status: 'completed',
        statusMessage: 'Cosine similarity against existing memory = 0.95 (>=0.92 threshold) -> NOOP DISCARDED!',
        durationMs: 14
      },
      qdrant_vault: {
        stageId: 'qdrant_vault',
        status: 'bypassed',
        statusMessage: 'NOOP decision -> Vector write skipped. Database remains clean.',
        durationMs: 0
      },
      neo4j_graph: {
        stageId: 'neo4j_graph',
        status: 'bypassed',
        statusMessage: 'Neo4j graph write skipped.',
        durationMs: 0
      },
      fused_retrieval: {
        stageId: 'fused_retrieval',
        status: 'completed',
        statusMessage: 'Existing memory retained without duplicate proliferation.',
        durationMs: 3
      }
    }
  }
];

export const CAMERA_PRESETS: CameraViewPreset[] = [
  {
    name: 'Full Pipeline Arc',
    position: [2, 16, 28],
    target: [2, 1, -2]
  },
  {
    name: 'Top Schematic',
    position: [2, 32, 0],
    target: [2, 0, -2]
  },
  {
    name: 'Isometric Stark Lab',
    position: [-22, 14, 18],
    target: [0, 1, -2]
  },
  {
    name: 'Vector & Storage Core',
    position: [6, 8, 12],
    target: [5, 1, -3]
  }
];
