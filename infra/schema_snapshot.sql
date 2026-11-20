-- Schema snapshot (E-08, plan section 5.3).
--
-- What this is: `pg_dump -s` of an empty database after applying
-- every migration in puspa/migrations/versions/. It is the committed
-- proof that the migrations reproduce the schema: the CI migration
-- test applies the migrations to a fresh database, dumps it the same
-- way, and compares (order-insensitive, comments stripped).
--
-- Regenerate after any migration lands (never hand-edit):
--
--     createdb puspa_snapshot_tmp
--     python -m puspa.migrations up --dsn "dbname=puspa_snapshot_tmp ..."
--     pg_dump -s "dbname=puspa_snapshot_tmp ..." > infra/schema_snapshot.sql
--     # then re-add this header block on top of the pg_dump output
--     dropdb puspa_snapshot_tmp
--
-- Generated output: the file-size ratchet skips it (see
-- tests/test_file_size_ratchet.py). The migrations are the source of
-- truth; this file is only the comparison target.
--
--
-- PostgreSQL database dump
--
-- PostgreSQL database dump
--

\restrict h9qUhleswHjHBhgmGfFNCddu9JuqwMaUKwlwsg50kCrbStfE2oCY20rec5ZfKmM

--
-- PostgreSQL database dump
--

\restrict 08RSOKFLqDPMddy0sffqJiZ6ALpkpwxZVhPMvkzcpx7eUq9SSc1vG7RqDweRfBR

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: is_room_member(uuid, uuid); Type: FUNCTION; Schema: public; Owner: root
--

CREATE FUNCTION public.is_room_member(p_room_id uuid, p_user_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    AS $$
    SELECT EXISTS (
        SELECT 1 FROM room_members
        WHERE room_id = p_room_id AND user_id = p_user_id
    );
$$;


ALTER FUNCTION public.is_room_member(p_room_id uuid, p_user_id uuid) OWNER TO root;

--
-- Name: is_room_owner(uuid, uuid); Type: FUNCTION; Schema: public; Owner: root
--

CREATE FUNCTION public.is_room_owner(p_room_id uuid, p_user_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    AS $$
    SELECT EXISTS (
        SELECT 1 FROM room_members
        WHERE room_id = p_room_id AND user_id = p_user_id AND role = 'owner'
    );
$$;


ALTER FUNCTION public.is_room_owner(p_room_id uuid, p_user_id uuid) OWNER TO root;

--
-- Name: is_team_member(uuid, uuid); Type: FUNCTION; Schema: public; Owner: root
--

CREATE FUNCTION public.is_team_member(p_team_id uuid, p_user_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.team_members m
        WHERE m.team_id = p_team_id AND m.user_id = p_user_id
    );
$$;


ALTER FUNCTION public.is_team_member(p_team_id uuid, p_user_id uuid) OWNER TO root;

--
-- Name: is_team_owner(uuid, uuid); Type: FUNCTION; Schema: public; Owner: root
--

CREATE FUNCTION public.is_team_owner(p_team_id uuid, p_user_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.team_members m
        WHERE m.team_id = p_team_id AND m.user_id = p_user_id AND m.role = 'owner'
    );
$$;


ALTER FUNCTION public.is_team_owner(p_team_id uuid, p_user_id uuid) OWNER TO root;

--
-- Name: tolak_delete_puspa(); Type: FUNCTION; Schema: public; Owner: root
--

CREATE FUNCTION public.tolak_delete_puspa() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.required THEN
        RAISE EXCEPTION 'Puspa tidak bisa dihapus';
    END IF;
    RETURN OLD;
END;
$$;


ALTER FUNCTION public.tolak_delete_puspa() OWNER TO root;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_connectors; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.agent_connectors (
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    connector text NOT NULL,
    scope text[] DEFAULT '{}'::text[] NOT NULL,
    terhubung boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.agent_connectors OWNER TO root;

--
-- Name: agent_goal_models; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.agent_goal_models (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    contact_id text NOT NULL,
    goals jsonb DEFAULT '{}'::jsonb NOT NULL,
    observations integer DEFAULT 0 NOT NULL,
    replies integer DEFAULT 0 NOT NULL,
    reply_delays jsonb DEFAULT '[]'::jsonb NOT NULL,
    channels jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_day integer,
    predictions jsonb DEFAULT '[]'::jsonb NOT NULL,
    stats jsonb DEFAULT '{"correct": 0, "predictions": 0}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_goal_models_check CHECK ((replies <= observations)),
    CONSTRAINT agent_goal_models_observations_check CHECK ((observations >= 0)),
    CONSTRAINT agent_goal_models_replies_check CHECK ((replies >= 0))
);


ALTER TABLE public.agent_goal_models OWNER TO root;

--
-- Name: agent_meta; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.agent_meta (
    agent_id text NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    peran text DEFAULT ''::text NOT NULL,
    purpose text DEFAULT ''::text NOT NULL,
    required boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.agent_meta OWNER TO root;

--
-- Name: agent_revocations; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.agent_revocations (
    agent_id text NOT NULL,
    revoked_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    actor text DEFAULT ''::text NOT NULL
);


ALTER TABLE public.agent_revocations OWNER TO root;

--
-- Name: agents; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.agents (
    agent_id text NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.agents OWNER TO root;

--
-- Name: belief_versions; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.belief_versions (
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    belief_key text NOT NULL,
    version integer NOT NULL,
    value jsonb DEFAULT '{}'::jsonb NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    known_from timestamp with time zone NOT NULL,
    known_to timestamp with time zone,
    supersedes integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT belief_versions_check CHECK (((valid_to IS NULL) OR (valid_to > valid_from))),
    CONSTRAINT belief_versions_check1 CHECK (((known_to IS NULL) OR (known_to > known_from)))
);


ALTER TABLE public.belief_versions OWNER TO root;

--
-- Name: billing_usage_rows; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.billing_usage_rows (
    id bigint NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    user_id text NOT NULL,
    bot_id text NOT NULL,
    thread_id text DEFAULT ''::text NOT NULL,
    task_id text DEFAULT ''::text NOT NULL,
    driver text DEFAULT ''::text NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    input_tokens bigint DEFAULT 0 NOT NULL,
    output_tokens bigint DEFAULT 0 NOT NULL,
    cached_tokens bigint DEFAULT 0 NOT NULL,
    cost_idr bigint DEFAULT 0 NOT NULL,
    trigger text DEFAULT ''::text NOT NULL,
    outcome text DEFAULT ''::text NOT NULL,
    steps integer DEFAULT 0 NOT NULL,
    tools jsonb DEFAULT '[]'::jsonb NOT NULL,
    tool_errors integer DEFAULT 0 NOT NULL,
    currency text DEFAULT 'IDR'::text NOT NULL,
    provider text DEFAULT ''::text NOT NULL,
    module_id text DEFAULT ''::text NOT NULL
);


ALTER TABLE public.billing_usage_rows OWNER TO root;

--
-- Name: billing_usage_rows_id_seq; Type: SEQUENCE; Schema: public; Owner: root
--

CREATE SEQUENCE public.billing_usage_rows_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.billing_usage_rows_id_seq OWNER TO root;

--
-- Name: billing_usage_rows_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: root
--

ALTER SEQUENCE public.billing_usage_rows_id_seq OWNED BY public.billing_usage_rows.id;


--
-- Name: block_requests; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.block_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    block text NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.block_requests OWNER TO root;

--
-- Name: bot_access_grants; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.bot_access_grants (
    bot_id text NOT NULL,
    user_id uuid NOT NULL,
    team_id uuid,
    granted_by uuid NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.bot_access_grants OWNER TO root;

--
-- Name: bot_access_requests; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.bot_access_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    bot_id text NOT NULL,
    team_id uuid NOT NULL,
    requester_user_id uuid NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    decided_by uuid,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bot_access_requests_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'denied'::text])))
);


ALTER TABLE public.bot_access_requests OWNER TO root;

--
-- Name: canary_runs; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.canary_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    prosedur_id uuid NOT NULL,
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    success boolean NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    jalan_pada timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.canary_runs OWNER TO root;

--
-- Name: canonical_products; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.canonical_products (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    brand text NOT NULL,
    series text DEFAULT ''::text NOT NULL,
    model_number text NOT NULL,
    category text NOT NULL,
    variant jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT canonical_products_category_check CHECK ((category = ANY (ARRAY['phone'::text, 'laptop'::text, 'tablet'::text, 'smartwatch'::text, 'earphones/TWS'::text, 'TV'::text, 'game console'::text, 'camera'::text, 'branded home appliances'::text])))
);


ALTER TABLE public.canonical_products OWNER TO root;

--
-- Name: cassettes; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.cassettes (
    id text NOT NULL,
    task_id text NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    berkas text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.cassettes OWNER TO root;

--
-- Name: causal_hypotheses; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.causal_hypotheses (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    cause text NOT NULL,
    effect text NOT NULL,
    effect_value jsonb DEFAULT 'null'::jsonb NOT NULL,
    condition text DEFAULT ''::text NOT NULL,
    strength double precision DEFAULT 0.5 NOT NULL,
    evidence jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    status text DEFAULT 'active'::text NOT NULL,
    supporting integer DEFAULT 0 NOT NULL,
    contradicting integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT causal_hypotheses_check CHECK (((valid_to IS NULL) OR (valid_to > valid_from))),
    CONSTRAINT causal_hypotheses_contradicting_check CHECK ((contradicting >= 0)),
    CONSTRAINT causal_hypotheses_status_check CHECK ((status = ANY (ARRAY['active'::text, 'expired'::text, 'contradicted'::text]))),
    CONSTRAINT causal_hypotheses_strength_check CHECK (((strength >= (0)::double precision) AND (strength <= (1)::double precision))),
    CONSTRAINT causal_hypotheses_supporting_check CHECK ((supporting >= 0))
);


ALTER TABLE public.causal_hypotheses OWNER TO root;

--
-- Name: channel_deliveries; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.channel_deliveries (
    id text NOT NULL,
    platform text NOT NULL,
    chat_id text NOT NULL,
    direction text NOT NULL,
    status text NOT NULL,
    dedup_key text NOT NULL,
    attempts integer DEFAULT 1 NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT channel_deliveries_attempts_check CHECK ((attempts >= 1)),
    CONSTRAINT channel_deliveries_direction_check CHECK ((direction = ANY (ARRAY['inbound'::text, 'outbound'::text]))),
    CONSTRAINT channel_deliveries_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'sent'::text, 'failed'::text, 'retrying'::text])))
);


ALTER TABLE public.channel_deliveries OWNER TO root;

--
-- Name: conflict_records; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.conflict_records (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    kind text NOT NULL,
    tool_name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    beneficiary text NOT NULL,
    user_interest text DEFAULT ''::text NOT NULL,
    other_interest text DEFAULT ''::text NOT NULL,
    user_cost text DEFAULT ''::text NOT NULL,
    severity text DEFAULT 'low'::text NOT NULL,
    status text DEFAULT 'surfaced'::text NOT NULL,
    user_choice text,
    surfaced_text text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    CONSTRAINT conflict_records_beneficiary_check CHECK ((beneficiary = ANY (ARRAY['user'::text, 'seller'::text, 'provider'::text, 'system'::text, 'other_person'::text]))),
    CONSTRAINT conflict_records_check CHECK (((resolved_at IS NULL) OR (status <> 'surfaced'::text))),
    CONSTRAINT conflict_records_kind_check CHECK ((kind = ANY (ARRAY['engagement_vs_goal'::text, 'seller_vs_user'::text, 'provider_vs_user'::text, 'retention_vs_independence'::text, 'attention_vs_benefit'::text]))),
    CONSTRAINT conflict_records_severity_check CHECK ((severity = ANY (ARRAY['low'::text, 'high'::text]))),
    CONSTRAINT conflict_records_status_check CHECK ((status = ANY (ARRAY['surfaced'::text, 'resolved_proceed'::text, 'resolved_alternative'::text, 'resolved_declined'::text]))),
    CONSTRAINT conflict_records_user_choice_check CHECK (((user_choice IS NULL) OR (user_choice = ANY (ARRAY['proceed'::text, 'alternative'::text, 'decline'::text]))))
);


ALTER TABLE public.conflict_records OWNER TO root;

--
-- Name: data_consents; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.data_consents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    text_version text NOT NULL,
    approved_at timestamp with time zone DEFAULT now() NOT NULL,
    event_id uuid,
    withdrawn_at timestamp with time zone
);


ALTER TABLE public.data_consents OWNER TO root;

--
-- Name: delegated_task_steps; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.delegated_task_steps (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    description text NOT NULL,
    tool_name text NOT NULL,
    irreversible boolean DEFAULT false NOT NULL,
    status text DEFAULT 'proposed'::text NOT NULL,
    content jsonb DEFAULT '{}'::jsonb NOT NULL,
    approval_request_id text,
    approved_by uuid,
    decided_at timestamp with time zone,
    executed_at timestamp with time zone,
    result text,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT delegated_task_steps_status_check CHECK ((status = ANY (ARRAY['proposed'::text, 'awaiting_approval'::text, 'approved'::text, 'rejected'::text, 'executed'::text])))
);


ALTER TABLE public.delegated_task_steps OWNER TO root;

--
-- Name: delegated_tasks; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.delegated_tasks (
    task_id uuid NOT NULL,
    room_id uuid NOT NULL,
    delegate_kind text NOT NULL,
    assignee_ref text NOT NULL,
    status text DEFAULT 'delegated'::text NOT NULL,
    last_report text,
    reported_at timestamp with time zone,
    started_at timestamp with time zone,
    blocked_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT delegated_tasks_delegate_kind_check CHECK ((delegate_kind = ANY (ARRAY['agent'::text, 'teammate'::text]))),
    CONSTRAINT delegated_tasks_status_check CHECK ((status = ANY (ARRAY['delegated'::text, 'in_progress'::text, 'awaiting_approval'::text, 'blocked'::text, 'done'::text])))
);


ALTER TABLE public.delegated_tasks OWNER TO root;

--
-- Name: draft_approvals; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.draft_approvals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    draft_id uuid NOT NULL,
    user_id uuid NOT NULL,
    content_hash text NOT NULL,
    status text DEFAULT 'granted'::text NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '24:00:00'::interval) NOT NULL,
    consumed_at timestamp with time zone,
    CONSTRAINT draft_approvals_status_check CHECK ((status = ANY (ARRAY['granted'::text, 'consumed'::text])))
);


ALTER TABLE public.draft_approvals OWNER TO root;

--
-- Name: drafts; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.drafts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    kind text NOT NULL,
    brief text NOT NULL,
    text text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    recipient text,
    channel text,
    decision_flags jsonb DEFAULT '[]'::jsonb NOT NULL,
    related_decisions jsonb DEFAULT '[]'::jsonb NOT NULL,
    voice_rules_applied jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    sent_at timestamp with time zone,
    CONSTRAINT drafts_kind_check CHECK ((kind = ANY (ARRAY['message'::text, 'document'::text, 'email'::text]))),
    CONSTRAINT drafts_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'approved'::text, 'sent'::text])))
);


ALTER TABLE public.drafts OWNER TO root;

--
-- Name: embedding_canary_baselines; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.embedding_canary_baselines (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    embedder_id text NOT NULL,
    dimension integer NOT NULL,
    recall double precision NOT NULL,
    question_count integer NOT NULL,
    measured_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT embedding_canary_baselines_dimension_check CHECK ((dimension > 0)),
    CONSTRAINT embedding_canary_baselines_question_count_check CHECK ((question_count > 0)),
    CONSTRAINT embedding_canary_baselines_recall_check CHECK (((recall >= (0)::double precision) AND (recall <= (1)::double precision)))
);


ALTER TABLE public.embedding_canary_baselines OWNER TO root;

--
-- Name: embedding_reembed_jobs; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.embedding_reembed_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    from_embedder_id text NOT NULL,
    to_embedder_id text NOT NULL,
    from_dimension integer NOT NULL,
    to_dimension integer NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    total_rows integer DEFAULT 0 NOT NULL,
    done_rows integer DEFAULT 0 NOT NULL,
    current_tier integer DEFAULT 0 NOT NULL,
    tiers jsonb DEFAULT '[]'::jsonb NOT NULL,
    error text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT embedding_reembed_jobs_current_tier_check CHECK ((current_tier >= 0)),
    CONSTRAINT embedding_reembed_jobs_done_rows_check CHECK ((done_rows >= 0)),
    CONSTRAINT embedding_reembed_jobs_from_dimension_check CHECK ((from_dimension > 0)),
    CONSTRAINT embedding_reembed_jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'done'::text, 'failed'::text]))),
    CONSTRAINT embedding_reembed_jobs_to_dimension_check CHECK ((to_dimension > 0)),
    CONSTRAINT embedding_reembed_jobs_total_rows_check CHECK ((total_rows >= 0))
);


ALTER TABLE public.embedding_reembed_jobs OWNER TO root;

--
-- Name: embedding_state; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.embedding_state (
    id integer DEFAULT 1 NOT NULL,
    active_embedder_id text DEFAULT 'hash'::text NOT NULL,
    active_dimension integer DEFAULT 384 NOT NULL,
    status text DEFAULT 'healthy'::text NOT NULL,
    fallback_count integer DEFAULT 0 NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT embedding_state_active_dimension_check CHECK ((active_dimension > 0)),
    CONSTRAINT embedding_state_fallback_count_check CHECK ((fallback_count >= 0)),
    CONSTRAINT embedding_state_id_check CHECK ((id = 1)),
    CONSTRAINT embedding_state_status_check CHECK ((status = ANY (ARRAY['healthy'::text, 'degraded'::text])))
);


ALTER TABLE public.embedding_state OWNER TO root;

--
-- Name: entitlements; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.entitlements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    machine_id text NOT NULL,
    team_id uuid NOT NULL,
    tier text NOT NULL,
    valid_until timestamp with time zone NOT NULL,
    revoked boolean DEFAULT false NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.entitlements OWNER TO root;

--
-- Name: episodic; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.episodic (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    agent_id text,
    task_id uuid,
    type text NOT NULL,
    content text NOT NULL,
    embedding public.vector(384),
    event_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    embedder_id text DEFAULT 'hash'::text NOT NULL,
    dimension integer DEFAULT 384 NOT NULL,
    CONSTRAINT episodic_dimension_positive CHECK ((dimension > 0))
);


ALTER TABLE public.episodic OWNER TO root;

--
-- Name: event_receipts; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.event_receipts (
    idempotency_key text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.event_receipts OWNER TO root;

--
-- Name: events; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    task_id text NOT NULL,
    agent_id text NOT NULL,
    subagent_id text,
    type text NOT NULL,
    content jsonb DEFAULT '{}'::jsonb NOT NULL,
    parent_id uuid,
    previous_hash text NOT NULL,
    hash text NOT NULL,
    valid_at timestamp with time zone,
    CONSTRAINT event_jenis_check CHECK ((type = ANY (ARRAY['task_created'::text, 'plan_created'::text, 'subagent_created'::text, 'handoff_proposed'::text, 'handoff_accepted'::text, 'handoff_handed_over'::text, 'handoff_rejected'::text, 'handoff_expired'::text, 'heartbeat'::text, 'tool_called'::text, 'tool_completed'::text, 'evidence_recorded'::text, 'verification_completed'::text, 'approval_requested'::text, 'approval_granted'::text, 'approval_rejected'::text, 'user_message'::text, 'agent_message'::text, 'budget_exhausted'::text, 'task_completed'::text, 'task_failed'::text, 'task_stopped'::text, 'workflow_proposed'::text, 'workflow_activated'::text, 'workflow_paused'::text, 'trigger_fired'::text, 'trigger_missed'::text, 'module_proposed'::text, 'module_activated'::text, 'false_report'::text, 'data_consent'::text]))),
    CONSTRAINT events_type_check CHECK ((type = ANY (ARRAY['task_created'::text, 'plan_created'::text, 'subagent_created'::text, 'handoff_proposed'::text, 'handoff_accepted'::text, 'handoff_handed_over'::text, 'handoff_rejected'::text, 'handoff_expired'::text, 'heartbeat'::text, 'tool_called'::text, 'tool_completed'::text, 'evidence_recorded'::text, 'verification_completed'::text, 'approval_requested'::text, 'approval_granted'::text, 'approval_rejected'::text, 'user_message'::text, 'agent_message'::text, 'budget_exhausted'::text, 'task_completed'::text, 'task_failed'::text, 'task_stopped'::text, 'workflow_proposed'::text, 'workflow_activated'::text, 'workflow_paused'::text, 'trigger_fired'::text, 'trigger_missed'::text, 'module_proposed'::text, 'module_activated'::text, 'false_report'::text, 'data_consent'::text])))
);


ALTER TABLE public.events OWNER TO root;

--
-- Name: evidence; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.evidence (
    evidence_id text NOT NULL,
    task_id text NOT NULL,
    user_id uuid NOT NULL,
    klaim text NOT NULL,
    value numeric NOT NULL,
    unit text DEFAULT 'Rp'::text NOT NULL,
    sumber_url text NOT NULL,
    time_take timestamp with time zone DEFAULT now() NOT NULL,
    text_excerpt text DEFAULT ''::text NOT NULL,
    page_hash text DEFAULT ''::text NOT NULL,
    screenshot text,
    reader_id text DEFAULT ''::text NOT NULL,
    verifier_id text,
    vonis text DEFAULT 'Uncertain'::text NOT NULL
);


ALTER TABLE public.evidence OWNER TO root;

--
-- Name: evidence_cache; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.evidence_cache (
    key text NOT NULL,
    sumber text NOT NULL,
    value jsonb NOT NULL,
    evidence_id text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    hit integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.evidence_cache OWNER TO root;

--
-- Name: false_reports; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.false_reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    evidence_id text NOT NULL,
    user_id uuid NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    reread_requested boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.false_reports OWNER TO root;

--
-- Name: handoffs; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.handoffs (
    id text NOT NULL,
    task_id text NOT NULL,
    user_id uuid NOT NULL,
    dari_id text NOT NULL,
    to_id text NOT NULL,
    purpose text NOT NULL,
    masukan jsonb DEFAULT '{}'::jsonb NOT NULL,
    expected_output jsonb DEFAULT '{}'::jsonb NOT NULL,
    timeout_seconds integer DEFAULT 45 NOT NULL,
    anggaran jsonb DEFAULT '{}'::jsonb NOT NULL,
    permission_class text NOT NULL,
    completion_definition text NOT NULL,
    idempotency_key text NOT NULL,
    status text DEFAULT 'proposed'::text NOT NULL,
    trial integer DEFAULT 1 NOT NULL,
    last_heartbeat timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    handed_over_at timestamp with time zone
);


ALTER TABLE public.handoffs OWNER TO root;

--
-- Name: invitations; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.invitations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    team_id uuid NOT NULL,
    inviter_user_id uuid NOT NULL,
    invited_email text NOT NULL,
    token_hash text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT invitations_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'accepted'::text, 'revoked'::text, 'expired'::text])))
);


ALTER TABLE public.invitations OWNER TO root;

--
-- Name: kill_switch; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.kill_switch (
    id integer DEFAULT 1 NOT NULL,
    active boolean DEFAULT false NOT NULL,
    actor text DEFAULT ''::text NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT kill_switch_id_check CHECK ((id = 1))
);


ALTER TABLE public.kill_switch OWNER TO root;

--
-- Name: listing_links; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.listing_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    product_id uuid NOT NULL,
    user_id uuid NOT NULL,
    marketplace text NOT NULL,
    listing_url text NOT NULL,
    status text DEFAULT 'automatic'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT listing_links_marketplace_check CHECK ((marketplace = ANY (ARRAY['Tokopedia'::text, 'Shopee'::text, 'Blibli'::text]))),
    CONSTRAINT listing_links_status_check CHECK ((status = ANY (ARRAY['automatic'::text, 'user_confirmed'::text, 'user_rejected'::text])))
);


ALTER TABLE public.listing_links OWNER TO root;

--
-- Name: loop_corrections; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.loop_corrections (
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    task text NOT NULL,
    predicted jsonb NOT NULL,
    observed jsonb NOT NULL,
    error text,
    source text,
    feedback_source text,
    correction_count integer DEFAULT 1 NOT NULL,
    experience_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.loop_corrections OWNER TO root;

--
-- Name: loop_experiences; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.loop_experiences (
    id text NOT NULL,
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    task text NOT NULL,
    kind text,
    source text,
    feedback_source text,
    verdict text,
    predicted jsonb,
    observed jsonb,
    error text,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    "position" integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.loop_experiences OWNER TO root;

--
-- Name: marketplace_audit; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.marketplace_audit (
    id bigint NOT NULL,
    event text NOT NULL,
    skill_id text NOT NULL,
    user_id text NOT NULL,
    sha256 text DEFAULT ''::text NOT NULL,
    source text DEFAULT ''::text NOT NULL,
    license text DEFAULT ''::text NOT NULL,
    price_credits integer DEFAULT 0 NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.marketplace_audit OWNER TO root;

--
-- Name: marketplace_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: root
--

CREATE SEQUENCE public.marketplace_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.marketplace_audit_id_seq OWNER TO root;

--
-- Name: marketplace_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: root
--

ALTER SEQUENCE public.marketplace_audit_id_seq OWNED BY public.marketplace_audit.id;


--
-- Name: marketplace_installs; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.marketplace_installs (
    skill_id text NOT NULL,
    user_id text NOT NULL,
    version text NOT NULL,
    sha256 text NOT NULL,
    source text NOT NULL,
    license text DEFAULT ''::text NOT NULL,
    price_credits integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'disabled'::text NOT NULL,
    installed_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.marketplace_installs OWNER TO root;

--
-- Name: marketplace_versions; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.marketplace_versions (
    skill_id text NOT NULL,
    user_id text NOT NULL,
    version text NOT NULL,
    sha256 text NOT NULL,
    source text NOT NULL,
    license text DEFAULT ''::text NOT NULL,
    price_credits integer DEFAULT 0 NOT NULL,
    manifest jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.marketplace_versions OWNER TO root;

--
-- Name: meeting_minutes; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.meeting_minutes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    room_id uuid NOT NULL,
    title text NOT NULL,
    held_at timestamp with time zone DEFAULT now() NOT NULL,
    author_user_id uuid NOT NULL,
    minutes_text text NOT NULL,
    decisions jsonb DEFAULT '[]'::jsonb NOT NULL,
    action_items jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.meeting_minutes OWNER TO root;

--
-- Name: mockups; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.mockups (
    id text NOT NULL,
    user_id text NOT NULL,
    title text NOT NULL,
    template text NOT NULL,
    doc jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.mockups OWNER TO root;

--
-- Name: model_settings; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.model_settings (
    user_id uuid NOT NULL,
    source text NOT NULL,
    base_url text,
    model text,
    provider text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.model_settings OWNER TO root;

--
-- Name: module_rows; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.module_rows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    table_id uuid NOT NULL,
    module_id uuid NOT NULL,
    user_id uuid NOT NULL,
    data jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.module_rows OWNER TO root;

--
-- Name: module_tables; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.module_tables (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    module_id uuid NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    kolom jsonb DEFAULT '[]'::jsonb NOT NULL
);


ALTER TABLE public.module_tables OWNER TO root;

--
-- Name: module_versions; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.module_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    module_id uuid NOT NULL,
    versi integer NOT NULL,
    manifest jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.module_versions OWNER TO root;

--
-- Name: modules; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.modules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    manifest jsonb NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    active_version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT module_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'paused'::text, 'deleted'::text])))
);


ALTER TABLE public.modules OWNER TO root;

--
-- Name: monthly_credits; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.monthly_credits (
    user_id uuid NOT NULL,
    month text NOT NULL,
    sisa integer DEFAULT 15 NOT NULL
);


ALTER TABLE public.monthly_credits OWNER TO root;

--
-- Name: morning_notes; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.morning_notes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    source text NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    raw_text text DEFAULT ''::text NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    actions jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT morning_notes_source_check CHECK ((source = ANY (ARRAY['voice'::text, 'text'::text, 'meeting'::text, 'lecture'::text, 'study'::text])))
);


ALTER TABLE public.morning_notes OWNER TO root;

--
-- Name: onboarding; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.onboarding (
    user_id uuid NOT NULL,
    current_step integer DEFAULT 0 NOT NULL,
    name text,
    style text,
    test_notification text,
    connector text DEFAULT 'nanti'::text NOT NULL,
    first_task text,
    first_result_verified boolean,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    duration_seconds numeric
);


ALTER TABLE public.onboarding OWNER TO root;

--
-- Name: onboarding_hint_seen; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.onboarding_hint_seen (
    user_id uuid NOT NULL,
    flag text NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.onboarding_hint_seen OWNER TO root;

--
-- Name: prepared_payments; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.prepared_payments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    recipient_name text NOT NULL,
    recipient_account text NOT NULL,
    recipient_verified_by text NOT NULL,
    amount_rupiah bigint NOT NULL,
    rail_id text NOT NULL,
    rail_display text NOT NULL,
    rail_kind text NOT NULL,
    rail_bank text DEFAULT ''::text NOT NULL,
    evidence jsonb DEFAULT '[]'::jsonb NOT NULL,
    idempotency_key text,
    status text DEFAULT 'awaiting_approval'::text NOT NULL,
    room_id uuid,
    task_id text DEFAULT ''::text NOT NULL,
    outcome_id text,
    approval_request_id text,
    prepared_by uuid NOT NULL,
    prepared_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    approved_at timestamp with time zone,
    approved_by uuid,
    paid_asserted_at timestamp with time zone,
    paid_asserted_by uuid,
    paid_assertion text,
    CONSTRAINT prepared_payments_amount_rupiah_check CHECK ((amount_rupiah > 0)),
    CONSTRAINT prepared_payments_rail_display_check CHECK ((rail_display <> ''::text)),
    CONSTRAINT prepared_payments_rail_id_check CHECK ((rail_id <> ''::text)),
    CONSTRAINT prepared_payments_rail_kind_check CHECK ((rail_kind <> ''::text)),
    CONSTRAINT prepared_payments_recipient_account_check CHECK ((recipient_account <> ''::text)),
    CONSTRAINT prepared_payments_recipient_name_check CHECK ((recipient_name <> ''::text)),
    CONSTRAINT prepared_payments_recipient_verified_by_check CHECK ((recipient_verified_by <> ''::text)),
    CONSTRAINT prepared_payments_status_check CHECK ((status = ANY (ARRAY['awaiting_approval'::text, 'approved'::text, 'rejected'::text, 'expired'::text, 'paid_by_user'::text])))
);


ALTER TABLE public.prepared_payments OWNER TO root;

--
-- Name: price_series; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.price_series (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tautan_id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    price numeric NOT NULL,
    shipping numeric DEFAULT 0 NOT NULL,
    store_rating numeric,
    stock text DEFAULT 'unknown'::text NOT NULL,
    evidence_id text NOT NULL
);


ALTER TABLE public.price_series OWNER TO root;

--
-- Name: procedure_uses; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.procedure_uses (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    prosedur_id uuid NOT NULL,
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    task_id text NOT NULL,
    used_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.procedure_uses OWNER TO root;

--
-- Name: procedure_versions; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.procedure_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    prosedur_id uuid NOT NULL,
    user_id uuid NOT NULL,
    versi integer NOT NULL,
    step jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.procedure_versions OWNER TO root;

--
-- Name: procedures; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.procedures (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    service text NOT NULL,
    versi integer DEFAULT 1 NOT NULL,
    step jsonb DEFAULT '[]'::jsonb NOT NULL,
    output_schema jsonb DEFAULT '{}'::jsonb NOT NULL,
    required_checks jsonb DEFAULT '[]'::jsonb NOT NULL,
    status text DEFAULT 'trial'::text NOT NULL,
    successful_runs integer DEFAULT 0 NOT NULL,
    failed_runs integer DEFAULT 0 NOT NULL,
    consecutive_failed_runs integer DEFAULT 0 NOT NULL,
    agent_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_event_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT procedures_status_check CHECK ((status = ANY (ARRAY['trial'::text, 'verified'::text, 'broken'::text])))
);


ALTER TABLE public.procedures OWNER TO root;

--
-- Name: profile_facts; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.profile_facts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    agent_id text,
    key text NOT NULL,
    value text NOT NULL,
    source text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone,
    superseded_by uuid,
    event_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    confidence real,
    source_detail text,
    CONSTRAINT profile_facts_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::double precision) AND (confidence <= (1)::double precision)))),
    CONSTRAINT profile_facts_source_check CHECK ((source = ANY (ARRAY['user_stated'::text, 'user_confirmed'::text, 'inferred'::text]))),
    CONSTRAINT profile_facts_status_check CHECK ((status = ANY (ARRAY['proposed'::text, 'active'::text, 'rejected'::text, 'replaced'::text, 'superseded'::text, 'expired'::text])))
);


ALTER TABLE public.profile_facts OWNER TO root;

--
-- Name: push_subscriptions; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.push_subscriptions (
    id text NOT NULL,
    user_id uuid NOT NULL,
    endpoint text NOT NULL,
    p256dh text NOT NULL,
    auth text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.push_subscriptions OWNER TO root;

--
-- Name: read_budgets; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.read_budgets (
    user_id uuid NOT NULL,
    date date NOT NULL,
    amount integer DEFAULT 0 NOT NULL
);


ALTER TABLE public.read_budgets OWNER TO root;

--
-- Name: room_channel_links; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.room_channel_links (
    platform text NOT NULL,
    chat_id text NOT NULL,
    room_id uuid NOT NULL,
    linked_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT room_channel_links_platform_check CHECK ((platform = ANY (ARRAY['telegram'::text, 'whatsapp'::text])))
);


ALTER TABLE public.room_channel_links OWNER TO root;

--
-- Name: room_invites; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.room_invites (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    room_id uuid NOT NULL,
    inviter_user_id uuid NOT NULL,
    kind text NOT NULL,
    role text NOT NULL,
    token_hash text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT room_invites_kind_check CHECK ((kind = ANY (ARRAY['link'::text, 'code'::text]))),
    CONSTRAINT room_invites_role_check CHECK ((role = ANY (ARRAY['member'::text, 'guest'::text]))),
    CONSTRAINT room_invites_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'accepted'::text, 'revoked'::text, 'expired'::text])))
);


ALTER TABLE public.room_invites OWNER TO root;

--
-- Name: room_job_queue; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.room_job_queue (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    room_id uuid NOT NULL,
    kind text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    requested_by uuid NOT NULL,
    result text,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    CONSTRAINT room_job_queue_kind_check CHECK ((kind = ANY (ARRAY['summarize'::text, 'note'::text]))),
    CONSTRAINT room_job_queue_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'processing'::text, 'done'::text, 'failed'::text])))
);


ALTER TABLE public.room_job_queue OWNER TO root;

--
-- Name: room_members; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.room_members (
    room_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role text NOT NULL,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT room_members_role_check CHECK ((role = ANY (ARRAY['owner'::text, 'member'::text, 'guest'::text])))
);


ALTER TABLE public.room_members OWNER TO root;

--
-- Name: room_memories; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.room_memories (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    room_id uuid NOT NULL,
    author_user_id uuid NOT NULL,
    kind text NOT NULL,
    content text NOT NULL,
    visibility text DEFAULT 'room'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT room_memories_kind_check CHECK ((kind = ANY (ARRAY['note'::text, 'decision'::text, 'summary'::text]))),
    CONSTRAINT room_memories_visibility_check CHECK ((visibility = ANY (ARRAY['room'::text, 'private'::text])))
);


ALTER TABLE public.room_memories OWNER TO root;

--
-- Name: room_money_approvals; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.room_money_approvals (
    action_id text NOT NULL,
    room_id uuid NOT NULL,
    approver_user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.room_money_approvals OWNER TO root;

--
-- Name: room_research_links; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.room_research_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    room_id uuid NOT NULL,
    url text NOT NULL,
    label text DEFAULT ''::text NOT NULL,
    source text NOT NULL,
    added_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.room_research_links OWNER TO root;

--
-- Name: room_splits; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.room_splits (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    room_id uuid NOT NULL,
    title text NOT NULL,
    kind text NOT NULL,
    total_cents bigint NOT NULL,
    payer_user_id uuid NOT NULL,
    created_by uuid NOT NULL,
    due_at timestamp with time zone,
    status text DEFAULT 'open'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    settled_at timestamp with time zone,
    CONSTRAINT room_splits_kind_check CHECK ((kind = ANY (ARRAY['meal'::text, 'gift'::text, 'arisan'::text, 'other'::text]))),
    CONSTRAINT room_splits_status_check CHECK ((status = ANY (ARRAY['open'::text, 'partially_paid'::text, 'settled'::text]))),
    CONSTRAINT room_splits_total_cents_check CHECK ((total_cents > 0))
);


ALTER TABLE public.room_splits OWNER TO root;

--
-- Name: room_tasks; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.room_tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    room_id uuid NOT NULL,
    title text NOT NULL,
    owner_user_id uuid,
    due_at timestamp with time zone,
    status text DEFAULT 'open'::text NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT room_tasks_status_check CHECK ((status = ANY (ARRAY['open'::text, 'done'::text])))
);


ALTER TABLE public.room_tasks OWNER TO root;

--
-- Name: rooms; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.rooms (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.rooms OWNER TO root;

--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.schema_migrations (
    version text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.schema_migrations OWNER TO root;

--
-- Name: sessions; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    device_label text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked boolean DEFAULT false NOT NULL
);


ALTER TABLE public.sessions OWNER TO root;

--
-- Name: shared_graph_private_records; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.shared_graph_private_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    kind text NOT NULL,
    subject_key text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.shared_graph_private_records OWNER TO root;

--
-- Name: shared_price_observations; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.shared_price_observations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    product_key text NOT NULL,
    source text NOT NULL,
    price numeric NOT NULL,
    observer_hash text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.shared_price_observations OWNER TO root;

--
-- Name: shared_seller_flags; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.shared_seller_flags (
    seller_key text NOT NULL,
    reporter_hash text NOT NULL,
    flag_reason text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.shared_seller_flags OWNER TO root;

--
-- Name: slice_grants; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.slice_grants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    agent_id text,
    subagent_id text,
    query text NOT NULL,
    kolom text[] DEFAULT '{}'::text[] NOT NULL,
    token_ref text NOT NULL,
    event_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.slice_grants OWNER TO root;

--
-- Name: source_readings; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.source_readings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    sumber text NOT NULL,
    category text DEFAULT ''::text NOT NULL,
    vonis text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT source_readings_vonis_check CHECK ((vonis = ANY (ARRAY['Verified'::text, 'Uncertain'::text, 'Cannot be determined'::text])))
);


ALTER TABLE public.source_readings OWNER TO root;

--
-- Name: split_payments; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.split_payments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    split_id uuid NOT NULL,
    debtor_user_id uuid NOT NULL,
    amount_cents bigint NOT NULL,
    recorded_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT split_payments_amount_cents_check CHECK ((amount_cents > 0))
);


ALTER TABLE public.split_payments OWNER TO root;

--
-- Name: split_settlements; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.split_settlements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    split_id uuid NOT NULL,
    debtor_user_id uuid NOT NULL,
    recipient_user_id uuid NOT NULL,
    amount_cents bigint NOT NULL,
    action_id text NOT NULL,
    status text DEFAULT 'prepared'::text NOT NULL,
    prepared_by uuid NOT NULL,
    prepared_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT split_settlements_amount_cents_check CHECK ((amount_cents > 0)),
    CONSTRAINT split_settlements_status_check CHECK ((status = ANY (ARRAY['prepared'::text, 'superseded'::text])))
);


ALTER TABLE public.split_settlements OWNER TO root;

--
-- Name: split_shares; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.split_shares (
    split_id uuid NOT NULL,
    user_id uuid NOT NULL,
    share_cents bigint NOT NULL,
    paid_cents bigint DEFAULT 0 NOT NULL,
    "position" integer DEFAULT 0 NOT NULL,
    CONSTRAINT split_shares_check CHECK ((paid_cents <= share_cents)),
    CONSTRAINT split_shares_paid_cents_check CHECK ((paid_cents >= 0)),
    CONSTRAINT split_shares_share_cents_check CHECK ((share_cents > 0))
);


ALTER TABLE public.split_shares OWNER TO root;

--
-- Name: style_examples; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.style_examples (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    text text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.style_examples OWNER TO root;

--
-- Name: summaries; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.summaries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    agent_id text,
    period_start timestamp with time zone NOT NULL,
    period_end timestamp with time zone NOT NULL,
    summary text NOT NULL,
    event_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.summaries OWNER TO root;

--
-- Name: system_heartbeats; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.system_heartbeats (
    komponen text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.system_heartbeats OWNER TO root;

--
-- Name: tasks; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.tasks (
    id text NOT NULL,
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    purpose text NOT NULL,
    status text DEFAULT 'accepted'::text NOT NULL,
    cost_rp integer DEFAULT 0 NOT NULL,
    step integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);


ALTER TABLE public.tasks OWNER TO root;

--
-- Name: team_members; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.team_members (
    team_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role text NOT NULL,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT team_members_role_check CHECK ((role = ANY (ARRAY['owner'::text, 'member'::text])))
);


ALTER TABLE public.team_members OWNER TO root;

--
-- Name: teams; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.teams (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.teams OWNER TO root;

--
-- Name: token_metrics; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.token_metrics (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id text NOT NULL,
    user_id uuid NOT NULL,
    type text NOT NULL,
    token integer DEFAULT 0 NOT NULL,
    dicatat timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.token_metrics OWNER TO root;

--
-- Name: token_revocations; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.token_revocations (
    jti text NOT NULL,
    agent_id text NOT NULL,
    revoked_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text DEFAULT ''::text NOT NULL
);


ALTER TABLE public.token_revocations OWNER TO root;

--
-- Name: tos_checks; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.tos_checks (
    sumber text NOT NULL,
    result text NOT NULL,
    pasal text DEFAULT ''::text NOT NULL,
    source_url text DEFAULT ''::text NOT NULL,
    checked_on date DEFAULT CURRENT_DATE NOT NULL,
    keputusan text DEFAULT 'awaiting_nero_decision'::text NOT NULL
);


ALTER TABLE public.tos_checks OWNER TO root;

--
-- Name: trigger_runs; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.trigger_runs (
    idempotency_key text NOT NULL,
    trigger_id text NOT NULL,
    task_id text NOT NULL,
    terlewat boolean DEFAULT false NOT NULL,
    jalan_pada timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.trigger_runs OWNER TO root;

--
-- Name: triggers; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.triggers (
    id text NOT NULL,
    user_id uuid NOT NULL,
    type text NOT NULL,
    schedule text DEFAULT ''::text NOT NULL,
    workflow text DEFAULT ''::text NOT NULL,
    next_trigger_at timestamp with time zone NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.triggers OWNER TO root;

--
-- Name: used_email_links; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.used_email_links (
    token_hash text NOT NULL,
    used_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.used_email_links OWNER TO root;

--
-- Name: users; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.users OWNER TO root;

--
-- Name: vault; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.vault (
    user_id uuid NOT NULL,
    name text NOT NULL,
    ciphertext text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.vault OWNER TO root;

--
-- Name: watchlist; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.watchlist (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    product_id uuid NOT NULL,
    price_threshold numeric,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.watchlist OWNER TO root;

--
-- Name: work_items; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.work_items (
    id text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    lease_owner text,
    lease_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.work_items OWNER TO root;

--
-- Name: workflow_approvals; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.workflow_approvals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    jalan_id uuid NOT NULL,
    workflow_id uuid NOT NULL,
    user_id uuid NOT NULL,
    action jsonb NOT NULL,
    content_hash text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT approval_workflow_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'granted'::text, 'rejected'::text, 'expired'::text, 'cancelled'::text])))
);


ALTER TABLE public.workflow_approvals OWNER TO root;

--
-- Name: workflow_library; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.workflow_library (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    block jsonb DEFAULT '[]'::jsonb NOT NULL,
    successful_runs integer DEFAULT 0 NOT NULL,
    active boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.workflow_library OWNER TO root;

--
-- Name: workflow_runs; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.workflow_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_id uuid NOT NULL,
    user_id uuid NOT NULL,
    task_id text NOT NULL,
    trigger_jenis text NOT NULL,
    status text DEFAULT 'completed'::text NOT NULL,
    cost_rp integer DEFAULT 0 NOT NULL,
    step_results jsonb DEFAULT '[]'::jsonb NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);


ALTER TABLE public.workflow_runs OWNER TO root;

--
-- Name: workflow_versions; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.workflow_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_id uuid NOT NULL,
    versi integer NOT NULL,
    trigger jsonb DEFAULT '{}'::jsonb NOT NULL,
    step jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.workflow_versions OWNER TO root;

--
-- Name: workflows; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.workflows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    name text NOT NULL,
    trigger jsonb DEFAULT '{}'::jsonb NOT NULL,
    step jsonb DEFAULT '[]'::jsonb NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    active_version integer DEFAULT 1 NOT NULL,
    consecutive_failures integer DEFAULT 0 NOT NULL,
    successful_runs integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workflow_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'paused'::text, 'deleted'::text])))
);


ALTER TABLE public.workflows OWNER TO root;

--
-- Name: workspace_moves; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.workspace_moves (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    bot_id text NOT NULL,
    team_id uuid NOT NULL,
    source_workspace text NOT NULL,
    target_workspace text NOT NULL,
    requested_by uuid NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    current_step text DEFAULT 'created'::text NOT NULL,
    error text,
    state jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workspace_moves_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'in_progress'::text, 'done'::text, 'failed'::text, 'cancelled'::text])))
);


ALTER TABLE public.workspace_moves OWNER TO root;

--
-- Name: world_beliefs; Type: TABLE; Schema: public; Owner: root
--

CREATE TABLE public.world_beliefs (
    user_id uuid NOT NULL,
    agent_id text NOT NULL,
    state jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.world_beliefs OWNER TO root;

--
-- Name: billing_usage_rows id; Type: DEFAULT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.billing_usage_rows ALTER COLUMN id SET DEFAULT nextval('public.billing_usage_rows_id_seq'::regclass);


--
-- Name: marketplace_audit id; Type: DEFAULT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.marketplace_audit ALTER COLUMN id SET DEFAULT nextval('public.marketplace_audit_id_seq'::regclass);


--
-- Name: agent_connectors agent_connectors_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_connectors
    ADD CONSTRAINT agent_connectors_pkey PRIMARY KEY (user_id, agent_id, connector);


--
-- Name: agent_goal_models agent_goal_models_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_goal_models
    ADD CONSTRAINT agent_goal_models_pkey PRIMARY KEY (id);


--
-- Name: agent_goal_models agent_goal_models_user_id_agent_id_contact_id_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_goal_models
    ADD CONSTRAINT agent_goal_models_user_id_agent_id_contact_id_key UNIQUE (user_id, agent_id, contact_id);


--
-- Name: agent_meta agent_meta_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_meta
    ADD CONSTRAINT agent_meta_pkey PRIMARY KEY (agent_id);


--
-- Name: agent_revocations agent_revocations_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_revocations
    ADD CONSTRAINT agent_revocations_pkey PRIMARY KEY (agent_id);


--
-- Name: agents agents_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (agent_id);


--
-- Name: belief_versions belief_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.belief_versions
    ADD CONSTRAINT belief_versions_pkey PRIMARY KEY (user_id, agent_id, belief_key, version);


--
-- Name: billing_usage_rows billing_usage_rows_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.billing_usage_rows
    ADD CONSTRAINT billing_usage_rows_pkey PRIMARY KEY (id);


--
-- Name: block_requests block_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.block_requests
    ADD CONSTRAINT block_requests_pkey PRIMARY KEY (id);


--
-- Name: bot_access_grants bot_access_grants_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.bot_access_grants
    ADD CONSTRAINT bot_access_grants_pkey PRIMARY KEY (bot_id, user_id);


--
-- Name: bot_access_requests bot_access_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.bot_access_requests
    ADD CONSTRAINT bot_access_requests_pkey PRIMARY KEY (id);


--
-- Name: canary_runs canary_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.canary_runs
    ADD CONSTRAINT canary_runs_pkey PRIMARY KEY (id);


--
-- Name: canonical_products canonical_products_brand_model_number_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.canonical_products
    ADD CONSTRAINT canonical_products_brand_model_number_key UNIQUE (brand, model_number);


--
-- Name: canonical_products canonical_products_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.canonical_products
    ADD CONSTRAINT canonical_products_pkey PRIMARY KEY (id);


--
-- Name: cassettes cassettes_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.cassettes
    ADD CONSTRAINT cassettes_pkey PRIMARY KEY (id);


--
-- Name: causal_hypotheses causal_hypotheses_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.causal_hypotheses
    ADD CONSTRAINT causal_hypotheses_pkey PRIMARY KEY (id);


--
-- Name: channel_deliveries channel_deliveries_dedup_key_direction_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.channel_deliveries
    ADD CONSTRAINT channel_deliveries_dedup_key_direction_key UNIQUE (dedup_key, direction);


--
-- Name: channel_deliveries channel_deliveries_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.channel_deliveries
    ADD CONSTRAINT channel_deliveries_pkey PRIMARY KEY (id);


--
-- Name: conflict_records conflict_records_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.conflict_records
    ADD CONSTRAINT conflict_records_pkey PRIMARY KEY (id);


--
-- Name: data_consents data_consents_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.data_consents
    ADD CONSTRAINT data_consents_pkey PRIMARY KEY (id);


--
-- Name: delegated_task_steps delegated_task_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.delegated_task_steps
    ADD CONSTRAINT delegated_task_steps_pkey PRIMARY KEY (id);


--
-- Name: delegated_tasks delegated_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.delegated_tasks
    ADD CONSTRAINT delegated_tasks_pkey PRIMARY KEY (task_id);


--
-- Name: draft_approvals draft_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.draft_approvals
    ADD CONSTRAINT draft_approvals_pkey PRIMARY KEY (id);


--
-- Name: drafts drafts_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.drafts
    ADD CONSTRAINT drafts_pkey PRIMARY KEY (id);


--
-- Name: embedding_canary_baselines embedding_canary_baselines_embedder_id_dimension_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.embedding_canary_baselines
    ADD CONSTRAINT embedding_canary_baselines_embedder_id_dimension_key UNIQUE (embedder_id, dimension);


--
-- Name: embedding_canary_baselines embedding_canary_baselines_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.embedding_canary_baselines
    ADD CONSTRAINT embedding_canary_baselines_pkey PRIMARY KEY (id);


--
-- Name: embedding_reembed_jobs embedding_reembed_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.embedding_reembed_jobs
    ADD CONSTRAINT embedding_reembed_jobs_pkey PRIMARY KEY (id);


--
-- Name: embedding_state embedding_state_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.embedding_state
    ADD CONSTRAINT embedding_state_pkey PRIMARY KEY (id);


--
-- Name: entitlements entitlements_machine_id_team_id_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.entitlements
    ADD CONSTRAINT entitlements_machine_id_team_id_key UNIQUE (machine_id, team_id);


--
-- Name: entitlements entitlements_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.entitlements
    ADD CONSTRAINT entitlements_pkey PRIMARY KEY (id);


--
-- Name: episodic episodic_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.episodic
    ADD CONSTRAINT episodic_pkey PRIMARY KEY (id);


--
-- Name: event_receipts event_receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.event_receipts
    ADD CONSTRAINT event_receipts_pkey PRIMARY KEY (idempotency_key);


--
-- Name: events events_hash_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_hash_key UNIQUE (hash);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: evidence_cache evidence_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.evidence_cache
    ADD CONSTRAINT evidence_cache_pkey PRIMARY KEY (key);


--
-- Name: evidence evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_pkey PRIMARY KEY (evidence_id);


--
-- Name: false_reports false_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.false_reports
    ADD CONSTRAINT false_reports_pkey PRIMARY KEY (id);


--
-- Name: handoffs handoffs_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.handoffs
    ADD CONSTRAINT handoffs_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: handoffs handoffs_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.handoffs
    ADD CONSTRAINT handoffs_pkey PRIMARY KEY (id);


--
-- Name: invitations invitations_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_pkey PRIMARY KEY (id);


--
-- Name: invitations invitations_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_token_hash_key UNIQUE (token_hash);


--
-- Name: kill_switch kill_switch_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.kill_switch
    ADD CONSTRAINT kill_switch_pkey PRIMARY KEY (id);


--
-- Name: listing_links listing_links_marketplace_listing_url_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.listing_links
    ADD CONSTRAINT listing_links_marketplace_listing_url_key UNIQUE (marketplace, listing_url);


--
-- Name: listing_links listing_links_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.listing_links
    ADD CONSTRAINT listing_links_pkey PRIMARY KEY (id);


--
-- Name: loop_corrections loop_corrections_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.loop_corrections
    ADD CONSTRAINT loop_corrections_pkey PRIMARY KEY (user_id, agent_id, task);


--
-- Name: loop_experiences loop_experiences_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.loop_experiences
    ADD CONSTRAINT loop_experiences_pkey PRIMARY KEY (id);


--
-- Name: marketplace_audit marketplace_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.marketplace_audit
    ADD CONSTRAINT marketplace_audit_pkey PRIMARY KEY (id);


--
-- Name: marketplace_installs marketplace_installs_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.marketplace_installs
    ADD CONSTRAINT marketplace_installs_pkey PRIMARY KEY (skill_id, user_id);


--
-- Name: marketplace_versions marketplace_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.marketplace_versions
    ADD CONSTRAINT marketplace_versions_pkey PRIMARY KEY (skill_id, user_id, version);


--
-- Name: meeting_minutes meeting_minutes_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.meeting_minutes
    ADD CONSTRAINT meeting_minutes_pkey PRIMARY KEY (id);


--
-- Name: mockups mockups_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.mockups
    ADD CONSTRAINT mockups_pkey PRIMARY KEY (id);


--
-- Name: model_settings model_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.model_settings
    ADD CONSTRAINT model_settings_pkey PRIMARY KEY (user_id);


--
-- Name: module_rows module_rows_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_rows
    ADD CONSTRAINT module_rows_pkey PRIMARY KEY (id);


--
-- Name: module_tables module_tables_module_id_name_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_tables
    ADD CONSTRAINT module_tables_module_id_name_key UNIQUE (module_id, name);


--
-- Name: module_tables module_tables_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_tables
    ADD CONSTRAINT module_tables_pkey PRIMARY KEY (id);


--
-- Name: module_versions module_versions_module_id_versi_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_versions
    ADD CONSTRAINT module_versions_module_id_versi_key UNIQUE (module_id, versi);


--
-- Name: module_versions module_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_versions
    ADD CONSTRAINT module_versions_pkey PRIMARY KEY (id);


--
-- Name: modules modules_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.modules
    ADD CONSTRAINT modules_pkey PRIMARY KEY (id);


--
-- Name: monthly_credits monthly_credits_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.monthly_credits
    ADD CONSTRAINT monthly_credits_pkey PRIMARY KEY (user_id, month);


--
-- Name: morning_notes morning_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.morning_notes
    ADD CONSTRAINT morning_notes_pkey PRIMARY KEY (id);


--
-- Name: onboarding_hint_seen onboarding_hint_seen_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.onboarding_hint_seen
    ADD CONSTRAINT onboarding_hint_seen_pkey PRIMARY KEY (user_id, flag);


--
-- Name: onboarding onboarding_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.onboarding
    ADD CONSTRAINT onboarding_pkey PRIMARY KEY (user_id);


--
-- Name: prepared_payments prepared_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.prepared_payments
    ADD CONSTRAINT prepared_payments_pkey PRIMARY KEY (id);


--
-- Name: prepared_payments prepared_payments_user_id_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.prepared_payments
    ADD CONSTRAINT prepared_payments_user_id_idempotency_key_key UNIQUE (user_id, idempotency_key);


--
-- Name: price_series price_series_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.price_series
    ADD CONSTRAINT price_series_pkey PRIMARY KEY (id);


--
-- Name: procedure_uses procedure_uses_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.procedure_uses
    ADD CONSTRAINT procedure_uses_pkey PRIMARY KEY (id);


--
-- Name: procedure_versions procedure_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.procedure_versions
    ADD CONSTRAINT procedure_versions_pkey PRIMARY KEY (id);


--
-- Name: procedure_versions procedure_versions_prosedur_id_versi_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.procedure_versions
    ADD CONSTRAINT procedure_versions_prosedur_id_versi_key UNIQUE (prosedur_id, versi);


--
-- Name: procedures procedures_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.procedures
    ADD CONSTRAINT procedures_pkey PRIMARY KEY (id);


--
-- Name: profile_facts profile_facts_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.profile_facts
    ADD CONSTRAINT profile_facts_pkey PRIMARY KEY (id);


--
-- Name: push_subscriptions push_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.push_subscriptions
    ADD CONSTRAINT push_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: read_budgets read_budgets_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.read_budgets
    ADD CONSTRAINT read_budgets_pkey PRIMARY KEY (user_id, date);


--
-- Name: room_channel_links room_channel_links_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_channel_links
    ADD CONSTRAINT room_channel_links_pkey PRIMARY KEY (platform, chat_id);


--
-- Name: room_invites room_invites_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_invites
    ADD CONSTRAINT room_invites_pkey PRIMARY KEY (id);


--
-- Name: room_invites room_invites_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_invites
    ADD CONSTRAINT room_invites_token_hash_key UNIQUE (token_hash);


--
-- Name: room_job_queue room_job_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_job_queue
    ADD CONSTRAINT room_job_queue_pkey PRIMARY KEY (id);


--
-- Name: room_members room_members_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_members
    ADD CONSTRAINT room_members_pkey PRIMARY KEY (room_id, user_id);


--
-- Name: room_memories room_memories_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_memories
    ADD CONSTRAINT room_memories_pkey PRIMARY KEY (id);


--
-- Name: room_money_approvals room_money_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_money_approvals
    ADD CONSTRAINT room_money_approvals_pkey PRIMARY KEY (action_id, room_id, approver_user_id);


--
-- Name: room_research_links room_research_links_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_research_links
    ADD CONSTRAINT room_research_links_pkey PRIMARY KEY (id);


--
-- Name: room_research_links room_research_links_room_id_url_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_research_links
    ADD CONSTRAINT room_research_links_room_id_url_key UNIQUE (room_id, url);


--
-- Name: room_splits room_splits_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_splits
    ADD CONSTRAINT room_splits_pkey PRIMARY KEY (id);


--
-- Name: room_tasks room_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_tasks
    ADD CONSTRAINT room_tasks_pkey PRIMARY KEY (id);


--
-- Name: rooms rooms_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.rooms
    ADD CONSTRAINT rooms_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_token_hash_key UNIQUE (token_hash);


--
-- Name: shared_graph_private_records shared_graph_private_records_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.shared_graph_private_records
    ADD CONSTRAINT shared_graph_private_records_pkey PRIMARY KEY (id);


--
-- Name: shared_price_observations shared_price_observations_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.shared_price_observations
    ADD CONSTRAINT shared_price_observations_pkey PRIMARY KEY (id);


--
-- Name: shared_price_observations shared_price_observations_product_key_source_observer_hash_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.shared_price_observations
    ADD CONSTRAINT shared_price_observations_product_key_source_observer_hash_key UNIQUE (product_key, source, observer_hash);


--
-- Name: shared_seller_flags shared_seller_flags_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.shared_seller_flags
    ADD CONSTRAINT shared_seller_flags_pkey PRIMARY KEY (seller_key, reporter_hash);


--
-- Name: slice_grants slice_grants_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.slice_grants
    ADD CONSTRAINT slice_grants_pkey PRIMARY KEY (id);


--
-- Name: source_readings source_readings_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.source_readings
    ADD CONSTRAINT source_readings_pkey PRIMARY KEY (id);


--
-- Name: split_payments split_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_payments
    ADD CONSTRAINT split_payments_pkey PRIMARY KEY (id);


--
-- Name: split_settlements split_settlements_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_settlements
    ADD CONSTRAINT split_settlements_pkey PRIMARY KEY (id);


--
-- Name: split_shares split_shares_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_shares
    ADD CONSTRAINT split_shares_pkey PRIMARY KEY (split_id, user_id);


--
-- Name: style_examples style_examples_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.style_examples
    ADD CONSTRAINT style_examples_pkey PRIMARY KEY (id);


--
-- Name: summaries summaries_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.summaries
    ADD CONSTRAINT summaries_pkey PRIMARY KEY (id);


--
-- Name: system_heartbeats system_heartbeats_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.system_heartbeats
    ADD CONSTRAINT system_heartbeats_pkey PRIMARY KEY (komponen);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: team_members team_members_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_pkey PRIMARY KEY (team_id, user_id);


--
-- Name: teams teams_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_pkey PRIMARY KEY (id);


--
-- Name: token_metrics token_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.token_metrics
    ADD CONSTRAINT token_metrics_pkey PRIMARY KEY (id);


--
-- Name: token_revocations token_revocations_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.token_revocations
    ADD CONSTRAINT token_revocations_pkey PRIMARY KEY (jti);


--
-- Name: tos_checks tos_checks_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.tos_checks
    ADD CONSTRAINT tos_checks_pkey PRIMARY KEY (sumber);


--
-- Name: trigger_runs trigger_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.trigger_runs
    ADD CONSTRAINT trigger_runs_pkey PRIMARY KEY (idempotency_key);


--
-- Name: triggers triggers_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.triggers
    ADD CONSTRAINT triggers_pkey PRIMARY KEY (id);


--
-- Name: used_email_links used_email_links_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.used_email_links
    ADD CONSTRAINT used_email_links_pkey PRIMARY KEY (token_hash);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: vault vault_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.vault
    ADD CONSTRAINT vault_pkey PRIMARY KEY (user_id, name);


--
-- Name: watchlist watchlist_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.watchlist
    ADD CONSTRAINT watchlist_pkey PRIMARY KEY (id);


--
-- Name: watchlist watchlist_user_id_product_id_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.watchlist
    ADD CONSTRAINT watchlist_user_id_product_id_key UNIQUE (user_id, product_id);


--
-- Name: work_items work_items_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.work_items
    ADD CONSTRAINT work_items_pkey PRIMARY KEY (id);


--
-- Name: workflow_approvals workflow_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_approvals
    ADD CONSTRAINT workflow_approvals_pkey PRIMARY KEY (id);


--
-- Name: workflow_library workflow_library_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_library
    ADD CONSTRAINT workflow_library_pkey PRIMARY KEY (id);


--
-- Name: workflow_runs workflow_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_runs
    ADD CONSTRAINT workflow_runs_pkey PRIMARY KEY (id);


--
-- Name: workflow_versions workflow_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_versions
    ADD CONSTRAINT workflow_versions_pkey PRIMARY KEY (id);


--
-- Name: workflow_versions workflow_versions_workflow_id_versi_key; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_versions
    ADD CONSTRAINT workflow_versions_workflow_id_versi_key UNIQUE (workflow_id, versi);


--
-- Name: workflows workflows_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflows
    ADD CONSTRAINT workflows_pkey PRIMARY KEY (id);


--
-- Name: workspace_moves workspace_moves_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workspace_moves
    ADD CONSTRAINT workspace_moves_pkey PRIMARY KEY (id);


--
-- Name: world_beliefs world_beliefs_pkey; Type: CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.world_beliefs
    ADD CONSTRAINT world_beliefs_pkey PRIMARY KEY (user_id, agent_id);


--
-- Name: channel_deliveries_chat_idx; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX channel_deliveries_chat_idx ON public.channel_deliveries USING btree (platform, chat_id, created_at DESC);


--
-- Name: channel_deliveries_dedup_idx; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX channel_deliveries_dedup_idx ON public.channel_deliveries USING btree (dedup_key);


--
-- Name: idx_agent_goal_models_contact; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_agent_goal_models_contact ON public.agent_goal_models USING btree (user_id, agent_id, contact_id);


--
-- Name: idx_agent_meta_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_agent_meta_user_id ON public.agent_meta USING btree (user_id);


--
-- Name: idx_agent_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_agent_user_id ON public.agents USING btree (user_id);


--
-- Name: idx_approval_user; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_approval_user ON public.data_consents USING btree (user_id, approved_at DESC);


--
-- Name: idx_approval_workflow_jalan; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_approval_workflow_jalan ON public.workflow_approvals USING btree (jalan_id, status);


--
-- Name: idx_belief_versions_known; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_belief_versions_known ON public.belief_versions USING btree (user_id, agent_id, belief_key, known_from, known_to);


--
-- Name: idx_belief_versions_valid; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_belief_versions_valid ON public.belief_versions USING btree (user_id, agent_id, belief_key, valid_from, valid_to);


--
-- Name: idx_billing_usage_bot_time; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_billing_usage_bot_time ON public.billing_usage_rows USING btree (bot_id, recorded_at);


--
-- Name: idx_billing_usage_task; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_billing_usage_task ON public.billing_usage_rows USING btree (user_id, task_id);


--
-- Name: idx_billing_usage_user_time; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_billing_usage_user_time ON public.billing_usage_rows USING btree (user_id, recorded_at);


--
-- Name: idx_bot_access_grants_team_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_bot_access_grants_team_id ON public.bot_access_grants USING btree (team_id);


--
-- Name: idx_bot_access_requests_status; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_bot_access_requests_status ON public.bot_access_requests USING btree (status);


--
-- Name: idx_bot_access_requests_team_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_bot_access_requests_team_id ON public.bot_access_requests USING btree (team_id);


--
-- Name: idx_cassette_task_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_cassette_task_id ON public.cassettes USING btree (task_id);


--
-- Name: idx_causal_hypotheses_cause; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_causal_hypotheses_cause ON public.causal_hypotheses USING btree (user_id, agent_id, cause, condition, status);


--
-- Name: idx_causal_hypotheses_expiry; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_causal_hypotheses_expiry ON public.causal_hypotheses USING btree (user_id, agent_id, expires_at) WHERE (status = 'active'::text);


--
-- Name: idx_causal_hypotheses_valid; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_causal_hypotheses_valid ON public.causal_hypotheses USING btree (user_id, agent_id, valid_from, valid_to);


--
-- Name: idx_conflict_records_unresolved; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_conflict_records_unresolved ON public.conflict_records USING btree (user_id, agent_id, created_at) WHERE (status = 'surfaced'::text);


--
-- Name: idx_conflict_records_user_tool; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_conflict_records_user_tool ON public.conflict_records USING btree (user_id, agent_id, tool_name, kind, created_at);


--
-- Name: idx_delegated_task_steps_status; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_delegated_task_steps_status ON public.delegated_task_steps USING btree (status);


--
-- Name: idx_delegated_task_steps_task_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_delegated_task_steps_task_id ON public.delegated_task_steps USING btree (task_id);


--
-- Name: idx_delegated_tasks_room_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_delegated_tasks_room_id ON public.delegated_tasks USING btree (room_id);


--
-- Name: idx_delegated_tasks_status; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_delegated_tasks_status ON public.delegated_tasks USING btree (status);


--
-- Name: idx_draft_approvals_draft_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_draft_approvals_draft_id ON public.draft_approvals USING btree (draft_id);


--
-- Name: idx_drafts_status; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_drafts_status ON public.drafts USING btree (user_id, status);


--
-- Name: idx_drafts_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_drafts_user_id ON public.drafts USING btree (user_id);


--
-- Name: idx_entitlements_team_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_entitlements_team_id ON public.entitlements USING btree (team_id);


--
-- Name: idx_episodic_embedder; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_episodic_embedder ON public.episodic USING btree (embedder_id, dimension);


--
-- Name: idx_episodik_embedding; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_episodik_embedding ON public.episodic USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: idx_episodik_user; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_episodik_user ON public.episodic USING btree (user_id, created_at DESC);


--
-- Name: idx_event_jenis; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_event_jenis ON public.events USING btree (type);


--
-- Name: idx_event_task_id_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_event_task_id_id ON public.events USING btree (task_id, id);


--
-- Name: idx_event_time; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_event_time ON public.events USING btree (created_at);


--
-- Name: idx_event_valid_at; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_event_valid_at ON public.events USING btree (valid_at);


--
-- Name: idx_events_outcome_room; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_events_outcome_room ON public.events USING btree (((content ->> 'kind'::text)), ((content ->> 'room_id'::text)));


--
-- Name: idx_events_outcome_user; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_events_outcome_user ON public.events USING btree (((content ->> 'kind'::text)), ((content ->> 'user_id'::text)));


--
-- Name: idx_evidence_task_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_evidence_task_id ON public.evidence USING btree (task_id);


--
-- Name: idx_evidence_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_evidence_user_id ON public.evidence USING btree (user_id);


--
-- Name: idx_example_gaya_user; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_example_gaya_user ON public.style_examples USING btree (user_id, created_at);


--
-- Name: idx_fact_user_key; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_fact_user_key ON public.profile_facts USING btree (user_id, key, status);


--
-- Name: idx_handoff_task_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_handoff_task_id ON public.handoffs USING btree (task_id);


--
-- Name: idx_invitations_team_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_invitations_team_id ON public.invitations USING btree (team_id);


--
-- Name: idx_irisan_user; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_irisan_user ON public.slice_grants USING btree (user_id, created_at DESC);


--
-- Name: idx_jalan_workflow_workflow; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_jalan_workflow_workflow ON public.workflow_runs USING btree (workflow_id, started_at DESC);


--
-- Name: idx_laporan_evidence; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_laporan_evidence ON public.false_reports USING btree (evidence_id);


--
-- Name: idx_meeting_minutes_room_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_meeting_minutes_room_id ON public.meeting_minutes USING btree (room_id);


--
-- Name: idx_metrik_token_jenis; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_metrik_token_jenis ON public.token_metrics USING btree (type, dicatat DESC);


--
-- Name: idx_module_user; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_module_user ON public.modules USING btree (user_id, status);


--
-- Name: idx_morning_notes_user_created; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_morning_notes_user_created ON public.morning_notes USING btree (user_id, created_at DESC);


--
-- Name: idx_pemakaian_prosedur; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_pemakaian_prosedur ON public.procedure_uses USING btree (prosedur_id, used_at DESC);


--
-- Name: idx_pembacaan_sumber; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_pembacaan_sumber ON public.source_readings USING btree (sumber, category, created_at DESC);


--
-- Name: idx_prepared_payments_expires_at; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_prepared_payments_expires_at ON public.prepared_payments USING btree (expires_at);


--
-- Name: idx_prepared_payments_status; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_prepared_payments_status ON public.prepared_payments USING btree (status);


--
-- Name: idx_prepared_payments_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_prepared_payments_user_id ON public.prepared_payments USING btree (user_id);


--
-- Name: idx_price_tautan_time; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_price_tautan_time ON public.price_series USING btree (tautan_id, created_at DESC);


--
-- Name: idx_private_records_user; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_private_records_user ON public.shared_graph_private_records USING btree (user_id, kind, created_at DESC);


--
-- Name: idx_product_category; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_product_category ON public.canonical_products USING btree (category);


--
-- Name: idx_product_number_model; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_product_number_model ON public.canonical_products USING btree (model_number);


--
-- Name: idx_prosedur_user_service; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_prosedur_user_service ON public.procedures USING btree (user_id, service, status);


--
-- Name: idx_push_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_push_user_id ON public.push_subscriptions USING btree (user_id);


--
-- Name: idx_reembed_jobs_status; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_reembed_jobs_status ON public.embedding_reembed_jobs USING btree (status, created_at DESC);


--
-- Name: idx_room_channel_links_room; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_channel_links_room ON public.room_channel_links USING btree (room_id);


--
-- Name: idx_room_invites_room_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_invites_room_id ON public.room_invites USING btree (room_id);


--
-- Name: idx_room_job_queue_room_status; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_job_queue_room_status ON public.room_job_queue USING btree (room_id, status, created_at);


--
-- Name: idx_room_members_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_members_user_id ON public.room_members USING btree (user_id);


--
-- Name: idx_room_memories_room_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_memories_room_id ON public.room_memories USING btree (room_id);


--
-- Name: idx_room_money_approvals_room; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_money_approvals_room ON public.room_money_approvals USING btree (room_id);


--
-- Name: idx_room_research_links_room_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_research_links_room_id ON public.room_research_links USING btree (room_id);


--
-- Name: idx_room_splits_room_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_splits_room_id ON public.room_splits USING btree (room_id);


--
-- Name: idx_room_tasks_owner; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_tasks_owner ON public.room_tasks USING btree (owner_user_id);


--
-- Name: idx_room_tasks_room_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_room_tasks_room_id ON public.room_tasks USING btree (room_id);


--
-- Name: idx_rows_module_table; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_rows_module_table ON public.module_rows USING btree (table_id, created_at DESC);


--
-- Name: idx_sesi_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_sesi_user_id ON public.sessions USING btree (user_id);


--
-- Name: idx_split_payments_split_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_split_payments_split_id ON public.split_payments USING btree (split_id);


--
-- Name: idx_split_settlements_split_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_split_settlements_split_id ON public.split_settlements USING btree (split_id);


--
-- Name: idx_split_shares_split_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_split_shares_split_id ON public.split_shares USING btree (split_id);


--
-- Name: idx_summary_user; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_summary_user ON public.summaries USING btree (user_id, created_at DESC);


--
-- Name: idx_task_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_task_user_id ON public.tasks USING btree (user_id);


--
-- Name: idx_tautan_product; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_tautan_product ON public.listing_links USING btree (product_id);


--
-- Name: idx_team_members_user_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_team_members_user_id ON public.team_members USING btree (user_id);


--
-- Name: idx_token_revocations_agent; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_token_revocations_agent ON public.token_revocations USING btree (agent_id, revoked_at);


--
-- Name: idx_trigger_berikutnya; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_trigger_berikutnya ON public.triggers USING btree (next_trigger_at) WHERE active;


--
-- Name: idx_work_items_claimable; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_work_items_claimable ON public.work_items USING btree (created_at) WHERE (status = 'pending'::text);


--
-- Name: idx_workflow_user; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_workflow_user ON public.workflows USING btree (user_id, status);


--
-- Name: idx_workspace_moves_bot_id; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_workspace_moves_bot_id ON public.workspace_moves USING btree (bot_id);


--
-- Name: idx_workspace_moves_status; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX idx_workspace_moves_status ON public.workspace_moves USING btree (status);


--
-- Name: mockups_user_idx; Type: INDEX; Schema: public; Owner: root
--

CREATE INDEX mockups_user_idx ON public.mockups USING btree (user_id, updated_at DESC);


--
-- Name: agent_meta trg_tolak_delete_puspa; Type: TRIGGER; Schema: public; Owner: root
--

CREATE TRIGGER trg_tolak_delete_puspa BEFORE DELETE ON public.agent_meta FOR EACH ROW EXECUTE FUNCTION public.tolak_delete_puspa();


--
-- Name: agent_connectors agent_connectors_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_connectors
    ADD CONSTRAINT agent_connectors_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: agent_connectors agent_connectors_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_connectors
    ADD CONSTRAINT agent_connectors_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: agent_goal_models agent_goal_models_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_goal_models
    ADD CONSTRAINT agent_goal_models_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: agent_goal_models agent_goal_models_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_goal_models
    ADD CONSTRAINT agent_goal_models_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: agent_meta agent_meta_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_meta
    ADD CONSTRAINT agent_meta_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: agent_meta agent_meta_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agent_meta
    ADD CONSTRAINT agent_meta_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: agents agents_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: belief_versions belief_versions_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.belief_versions
    ADD CONSTRAINT belief_versions_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: belief_versions belief_versions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.belief_versions
    ADD CONSTRAINT belief_versions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: block_requests block_requests_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.block_requests
    ADD CONSTRAINT block_requests_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: bot_access_grants bot_access_grants_granted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.bot_access_grants
    ADD CONSTRAINT bot_access_grants_granted_by_fkey FOREIGN KEY (granted_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: bot_access_grants bot_access_grants_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.bot_access_grants
    ADD CONSTRAINT bot_access_grants_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: bot_access_grants bot_access_grants_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.bot_access_grants
    ADD CONSTRAINT bot_access_grants_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: bot_access_requests bot_access_requests_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.bot_access_requests
    ADD CONSTRAINT bot_access_requests_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: bot_access_requests bot_access_requests_requester_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.bot_access_requests
    ADD CONSTRAINT bot_access_requests_requester_user_id_fkey FOREIGN KEY (requester_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: bot_access_requests bot_access_requests_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.bot_access_requests
    ADD CONSTRAINT bot_access_requests_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: canary_runs canary_runs_prosedur_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.canary_runs
    ADD CONSTRAINT canary_runs_prosedur_id_fkey FOREIGN KEY (prosedur_id) REFERENCES public.procedures(id) ON DELETE CASCADE;


--
-- Name: canary_runs canary_runs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.canary_runs
    ADD CONSTRAINT canary_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: cassettes cassettes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.cassettes
    ADD CONSTRAINT cassettes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: causal_hypotheses causal_hypotheses_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.causal_hypotheses
    ADD CONSTRAINT causal_hypotheses_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: causal_hypotheses causal_hypotheses_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.causal_hypotheses
    ADD CONSTRAINT causal_hypotheses_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: conflict_records conflict_records_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.conflict_records
    ADD CONSTRAINT conflict_records_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: conflict_records conflict_records_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.conflict_records
    ADD CONSTRAINT conflict_records_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: delegated_task_steps delegated_task_steps_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.delegated_task_steps
    ADD CONSTRAINT delegated_task_steps_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: delegated_task_steps delegated_task_steps_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.delegated_task_steps
    ADD CONSTRAINT delegated_task_steps_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: delegated_task_steps delegated_task_steps_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.delegated_task_steps
    ADD CONSTRAINT delegated_task_steps_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.room_tasks(id) ON DELETE CASCADE;


--
-- Name: delegated_tasks delegated_tasks_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.delegated_tasks
    ADD CONSTRAINT delegated_tasks_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: delegated_tasks delegated_tasks_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.delegated_tasks
    ADD CONSTRAINT delegated_tasks_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.room_tasks(id) ON DELETE CASCADE;


--
-- Name: draft_approvals draft_approvals_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.draft_approvals
    ADD CONSTRAINT draft_approvals_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.drafts(id) ON DELETE CASCADE;


--
-- Name: draft_approvals draft_approvals_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.draft_approvals
    ADD CONSTRAINT draft_approvals_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: drafts drafts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.drafts
    ADD CONSTRAINT drafts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: entitlements entitlements_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.entitlements
    ADD CONSTRAINT entitlements_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: events events_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.events(id);


--
-- Name: evidence evidence_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: false_reports false_reports_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.false_reports
    ADD CONSTRAINT false_reports_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: handoffs handoffs_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.handoffs
    ADD CONSTRAINT handoffs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON DELETE CASCADE;


--
-- Name: handoffs handoffs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.handoffs
    ADD CONSTRAINT handoffs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: invitations invitations_inviter_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_inviter_user_id_fkey FOREIGN KEY (inviter_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: invitations invitations_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: listing_links listing_links_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.listing_links
    ADD CONSTRAINT listing_links_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.canonical_products(id) ON DELETE CASCADE;


--
-- Name: listing_links listing_links_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.listing_links
    ADD CONSTRAINT listing_links_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: loop_corrections loop_corrections_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.loop_corrections
    ADD CONSTRAINT loop_corrections_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: loop_corrections loop_corrections_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.loop_corrections
    ADD CONSTRAINT loop_corrections_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: loop_experiences loop_experiences_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.loop_experiences
    ADD CONSTRAINT loop_experiences_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: loop_experiences loop_experiences_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.loop_experiences
    ADD CONSTRAINT loop_experiences_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: meeting_minutes meeting_minutes_author_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.meeting_minutes
    ADD CONSTRAINT meeting_minutes_author_user_id_fkey FOREIGN KEY (author_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: meeting_minutes meeting_minutes_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.meeting_minutes
    ADD CONSTRAINT meeting_minutes_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: model_settings model_settings_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.model_settings
    ADD CONSTRAINT model_settings_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: module_rows module_rows_module_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_rows
    ADD CONSTRAINT module_rows_module_id_fkey FOREIGN KEY (module_id) REFERENCES public.modules(id) ON DELETE CASCADE;


--
-- Name: module_rows module_rows_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_rows
    ADD CONSTRAINT module_rows_table_id_fkey FOREIGN KEY (table_id) REFERENCES public.module_tables(id) ON DELETE CASCADE;


--
-- Name: module_rows module_rows_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_rows
    ADD CONSTRAINT module_rows_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: module_tables module_tables_module_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_tables
    ADD CONSTRAINT module_tables_module_id_fkey FOREIGN KEY (module_id) REFERENCES public.modules(id) ON DELETE CASCADE;


--
-- Name: module_tables module_tables_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_tables
    ADD CONSTRAINT module_tables_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: module_versions module_versions_module_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.module_versions
    ADD CONSTRAINT module_versions_module_id_fkey FOREIGN KEY (module_id) REFERENCES public.modules(id) ON DELETE CASCADE;


--
-- Name: modules modules_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.modules
    ADD CONSTRAINT modules_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: monthly_credits monthly_credits_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.monthly_credits
    ADD CONSTRAINT monthly_credits_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: morning_notes morning_notes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.morning_notes
    ADD CONSTRAINT morning_notes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: onboarding_hint_seen onboarding_hint_seen_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.onboarding_hint_seen
    ADD CONSTRAINT onboarding_hint_seen_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: prepared_payments prepared_payments_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.prepared_payments
    ADD CONSTRAINT prepared_payments_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: prepared_payments prepared_payments_paid_asserted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.prepared_payments
    ADD CONSTRAINT prepared_payments_paid_asserted_by_fkey FOREIGN KEY (paid_asserted_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: prepared_payments prepared_payments_prepared_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.prepared_payments
    ADD CONSTRAINT prepared_payments_prepared_by_fkey FOREIGN KEY (prepared_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: prepared_payments prepared_payments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.prepared_payments
    ADD CONSTRAINT prepared_payments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: price_series price_series_tautan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.price_series
    ADD CONSTRAINT price_series_tautan_id_fkey FOREIGN KEY (tautan_id) REFERENCES public.listing_links(id) ON DELETE CASCADE;


--
-- Name: price_series price_series_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.price_series
    ADD CONSTRAINT price_series_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: procedure_uses procedure_uses_prosedur_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.procedure_uses
    ADD CONSTRAINT procedure_uses_prosedur_id_fkey FOREIGN KEY (prosedur_id) REFERENCES public.procedures(id) ON DELETE CASCADE;


--
-- Name: procedure_uses procedure_uses_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.procedure_uses
    ADD CONSTRAINT procedure_uses_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: procedure_versions procedure_versions_prosedur_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.procedure_versions
    ADD CONSTRAINT procedure_versions_prosedur_id_fkey FOREIGN KEY (prosedur_id) REFERENCES public.procedures(id) ON DELETE CASCADE;


--
-- Name: procedure_versions procedure_versions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.procedure_versions
    ADD CONSTRAINT procedure_versions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: push_subscriptions push_subscriptions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.push_subscriptions
    ADD CONSTRAINT push_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: read_budgets read_budgets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.read_budgets
    ADD CONSTRAINT read_budgets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_channel_links room_channel_links_linked_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_channel_links
    ADD CONSTRAINT room_channel_links_linked_by_fkey FOREIGN KEY (linked_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_channel_links room_channel_links_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_channel_links
    ADD CONSTRAINT room_channel_links_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: room_invites room_invites_inviter_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_invites
    ADD CONSTRAINT room_invites_inviter_user_id_fkey FOREIGN KEY (inviter_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_invites room_invites_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_invites
    ADD CONSTRAINT room_invites_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: room_job_queue room_job_queue_requested_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_job_queue
    ADD CONSTRAINT room_job_queue_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_job_queue room_job_queue_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_job_queue
    ADD CONSTRAINT room_job_queue_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: room_members room_members_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_members
    ADD CONSTRAINT room_members_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: room_members room_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_members
    ADD CONSTRAINT room_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_memories room_memories_author_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_memories
    ADD CONSTRAINT room_memories_author_user_id_fkey FOREIGN KEY (author_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_memories room_memories_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_memories
    ADD CONSTRAINT room_memories_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: room_money_approvals room_money_approvals_approver_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_money_approvals
    ADD CONSTRAINT room_money_approvals_approver_user_id_fkey FOREIGN KEY (approver_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_money_approvals room_money_approvals_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_money_approvals
    ADD CONSTRAINT room_money_approvals_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: room_research_links room_research_links_added_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_research_links
    ADD CONSTRAINT room_research_links_added_by_fkey FOREIGN KEY (added_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_research_links room_research_links_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_research_links
    ADD CONSTRAINT room_research_links_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: room_splits room_splits_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_splits
    ADD CONSTRAINT room_splits_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_splits room_splits_payer_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_splits
    ADD CONSTRAINT room_splits_payer_user_id_fkey FOREIGN KEY (payer_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_splits room_splits_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_splits
    ADD CONSTRAINT room_splits_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: room_tasks room_tasks_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_tasks
    ADD CONSTRAINT room_tasks_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: room_tasks room_tasks_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_tasks
    ADD CONSTRAINT room_tasks_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: room_tasks room_tasks_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.room_tasks
    ADD CONSTRAINT room_tasks_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(id) ON DELETE CASCADE;


--
-- Name: rooms rooms_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.rooms
    ADD CONSTRAINT rooms_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: shared_graph_private_records shared_graph_private_records_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.shared_graph_private_records
    ADD CONSTRAINT shared_graph_private_records_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: source_readings source_readings_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.source_readings
    ADD CONSTRAINT source_readings_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: split_payments split_payments_debtor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_payments
    ADD CONSTRAINT split_payments_debtor_user_id_fkey FOREIGN KEY (debtor_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: split_payments split_payments_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_payments
    ADD CONSTRAINT split_payments_recorded_by_fkey FOREIGN KEY (recorded_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: split_payments split_payments_split_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_payments
    ADD CONSTRAINT split_payments_split_id_fkey FOREIGN KEY (split_id) REFERENCES public.room_splits(id) ON DELETE CASCADE;


--
-- Name: split_settlements split_settlements_debtor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_settlements
    ADD CONSTRAINT split_settlements_debtor_user_id_fkey FOREIGN KEY (debtor_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: split_settlements split_settlements_prepared_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_settlements
    ADD CONSTRAINT split_settlements_prepared_by_fkey FOREIGN KEY (prepared_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: split_settlements split_settlements_recipient_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_settlements
    ADD CONSTRAINT split_settlements_recipient_user_id_fkey FOREIGN KEY (recipient_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: split_settlements split_settlements_split_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_settlements
    ADD CONSTRAINT split_settlements_split_id_fkey FOREIGN KEY (split_id) REFERENCES public.room_splits(id) ON DELETE CASCADE;


--
-- Name: split_shares split_shares_split_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_shares
    ADD CONSTRAINT split_shares_split_id_fkey FOREIGN KEY (split_id) REFERENCES public.room_splits(id) ON DELETE CASCADE;


--
-- Name: split_shares split_shares_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.split_shares
    ADD CONSTRAINT split_shares_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: tasks tasks_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: tasks tasks_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: team_members team_members_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_members team_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: teams teams_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: token_metrics token_metrics_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.token_metrics
    ADD CONSTRAINT token_metrics_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: trigger_runs trigger_runs_trigger_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.trigger_runs
    ADD CONSTRAINT trigger_runs_trigger_id_fkey FOREIGN KEY (trigger_id) REFERENCES public.triggers(id) ON DELETE CASCADE;


--
-- Name: triggers triggers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.triggers
    ADD CONSTRAINT triggers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: vault vault_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.vault
    ADD CONSTRAINT vault_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: watchlist watchlist_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.watchlist
    ADD CONSTRAINT watchlist_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.canonical_products(id) ON DELETE CASCADE;


--
-- Name: watchlist watchlist_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.watchlist
    ADD CONSTRAINT watchlist_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: workflow_approvals workflow_approvals_jalan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_approvals
    ADD CONSTRAINT workflow_approvals_jalan_id_fkey FOREIGN KEY (jalan_id) REFERENCES public.workflow_runs(id) ON DELETE CASCADE;


--
-- Name: workflow_approvals workflow_approvals_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_approvals
    ADD CONSTRAINT workflow_approvals_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: workflow_approvals workflow_approvals_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_approvals
    ADD CONSTRAINT workflow_approvals_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflows(id) ON DELETE CASCADE;


--
-- Name: workflow_library workflow_library_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_library
    ADD CONSTRAINT workflow_library_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: workflow_runs workflow_runs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_runs
    ADD CONSTRAINT workflow_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: workflow_runs workflow_runs_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_runs
    ADD CONSTRAINT workflow_runs_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflows(id) ON DELETE CASCADE;


--
-- Name: workflow_versions workflow_versions_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflow_versions
    ADD CONSTRAINT workflow_versions_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflows(id) ON DELETE CASCADE;


--
-- Name: workflows workflows_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workflows
    ADD CONSTRAINT workflows_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: workspace_moves workspace_moves_requested_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workspace_moves
    ADD CONSTRAINT workspace_moves_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: workspace_moves workspace_moves_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.workspace_moves
    ADD CONSTRAINT workspace_moves_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: world_beliefs world_beliefs_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.world_beliefs
    ADD CONSTRAINT world_beliefs_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(agent_id) ON DELETE CASCADE;


--
-- Name: world_beliefs world_beliefs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: root
--

ALTER TABLE ONLY public.world_beliefs
    ADD CONSTRAINT world_beliefs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: agent_connectors; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.agent_connectors ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_goal_models; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.agent_goal_models ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_meta; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.agent_meta ENABLE ROW LEVEL SECURITY;

--
-- Name: data_consents approval_data_own_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY approval_data_own_own ON public.data_consents USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: belief_versions; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.belief_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: block_requests; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.block_requests ENABLE ROW LEVEL SECURITY;

--
-- Name: bot_access_grants; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.bot_access_grants ENABLE ROW LEVEL SECURITY;

--
-- Name: bot_access_grants bot_access_grants_delete_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY bot_access_grants_delete_owner ON public.bot_access_grants FOR DELETE USING (((team_id IS NOT NULL) AND public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)));


--
-- Name: bot_access_grants bot_access_grants_view; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY bot_access_grants_view ON public.bot_access_grants FOR SELECT USING ((((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)) OR ((team_id IS NOT NULL) AND public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid))));


--
-- Name: bot_access_grants bot_access_grants_write_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY bot_access_grants_write_owner ON public.bot_access_grants FOR INSERT WITH CHECK (((team_id IS NOT NULL) AND public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)));


--
-- Name: bot_access_requests; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.bot_access_requests ENABLE ROW LEVEL SECURITY;

--
-- Name: bot_access_requests bot_access_requests_insert_member; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY bot_access_requests_insert_member ON public.bot_access_requests FOR INSERT WITH CHECK ((((requester_user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)) AND public.is_team_member(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)));


--
-- Name: bot_access_requests bot_access_requests_update_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY bot_access_requests_update_owner ON public.bot_access_requests FOR UPDATE USING (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)) WITH CHECK (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: bot_access_requests bot_access_requests_view; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY bot_access_requests_view ON public.bot_access_requests FOR SELECT USING ((((requester_user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)) OR public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)));


--
-- Name: canary_runs; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.canary_runs ENABLE ROW LEVEL SECURITY;

--
-- Name: canonical_products; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.canonical_products ENABLE ROW LEVEL SECURITY;

--
-- Name: cassettes; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.cassettes ENABLE ROW LEVEL SECURITY;

--
-- Name: causal_hypotheses; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.causal_hypotheses ENABLE ROW LEVEL SECURITY;

--
-- Name: conflict_records; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.conflict_records ENABLE ROW LEVEL SECURITY;

--
-- Name: data_consents; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.data_consents ENABLE ROW LEVEL SECURITY;

--
-- Name: delegated_task_steps; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.delegated_task_steps ENABLE ROW LEVEL SECURITY;

--
-- Name: delegated_task_steps delegated_task_steps_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY delegated_task_steps_delete ON public.delegated_task_steps FOR DELETE TO puspa_app USING ((EXISTS ( SELECT 1
   FROM public.room_tasks t
  WHERE ((t.id = delegated_task_steps.task_id) AND public.is_room_member(t.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: delegated_task_steps delegated_task_steps_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY delegated_task_steps_insert ON public.delegated_task_steps FOR INSERT TO puspa_app WITH CHECK ((EXISTS ( SELECT 1
   FROM public.room_tasks t
  WHERE ((t.id = delegated_task_steps.task_id) AND public.is_room_member(t.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: delegated_task_steps delegated_task_steps_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY delegated_task_steps_select ON public.delegated_task_steps FOR SELECT TO puspa_app USING ((EXISTS ( SELECT 1
   FROM public.room_tasks t
  WHERE ((t.id = delegated_task_steps.task_id) AND public.is_room_member(t.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: delegated_task_steps delegated_task_steps_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY delegated_task_steps_update ON public.delegated_task_steps FOR UPDATE TO puspa_app USING ((EXISTS ( SELECT 1
   FROM public.room_tasks t
  WHERE ((t.id = delegated_task_steps.task_id) AND public.is_room_member(t.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: delegated_tasks; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.delegated_tasks ENABLE ROW LEVEL SECURITY;

--
-- Name: delegated_tasks delegated_tasks_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY delegated_tasks_delete ON public.delegated_tasks FOR DELETE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: delegated_tasks delegated_tasks_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY delegated_tasks_insert ON public.delegated_tasks FOR INSERT TO puspa_app WITH CHECK (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: delegated_tasks delegated_tasks_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY delegated_tasks_select ON public.delegated_tasks FOR SELECT TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: delegated_tasks delegated_tasks_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY delegated_tasks_update ON public.delegated_tasks FOR UPDATE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: draft_approvals; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.draft_approvals ENABLE ROW LEVEL SECURITY;

--
-- Name: draft_approvals draft_approvals_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY draft_approvals_delete ON public.draft_approvals FOR DELETE TO puspa_app USING ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: draft_approvals draft_approvals_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY draft_approvals_insert ON public.draft_approvals FOR INSERT TO puspa_app WITH CHECK ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: draft_approvals draft_approvals_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY draft_approvals_select ON public.draft_approvals FOR SELECT TO puspa_app USING ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: draft_approvals draft_approvals_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY draft_approvals_update ON public.draft_approvals FOR UPDATE TO puspa_app USING ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: drafts; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.drafts ENABLE ROW LEVEL SECURITY;

--
-- Name: drafts drafts_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY drafts_delete ON public.drafts FOR DELETE TO puspa_app USING ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: drafts drafts_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY drafts_insert ON public.drafts FOR INSERT TO puspa_app WITH CHECK ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: drafts drafts_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY drafts_select ON public.drafts FOR SELECT TO puspa_app USING ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: drafts drafts_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY drafts_update ON public.drafts FOR UPDATE TO puspa_app USING ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: entitlements; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.entitlements ENABLE ROW LEVEL SECURITY;

--
-- Name: entitlements entitlements_delete_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY entitlements_delete_owner ON public.entitlements FOR DELETE USING (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: entitlements entitlements_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY entitlements_owner ON public.entitlements FOR SELECT USING (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: entitlements entitlements_update_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY entitlements_update_owner ON public.entitlements FOR UPDATE USING (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)) WITH CHECK (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: episodic; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.episodic ENABLE ROW LEVEL SECURITY;

--
-- Name: episodic episodik_own_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY episodik_own_own ON public.episodic USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: evidence; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.evidence ENABLE ROW LEVEL SECURITY;

--
-- Name: evidence_cache; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.evidence_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: style_examples example_gaya_own_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY example_gaya_own_own ON public.style_examples USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: profile_facts fact_profile_own_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY fact_profile_own_own ON public.profile_facts USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: false_reports; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.false_reports ENABLE ROW LEVEL SECURITY;

--
-- Name: handoffs; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.handoffs ENABLE ROW LEVEL SECURITY;

--
-- Name: invitations; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.invitations ENABLE ROW LEVEL SECURITY;

--
-- Name: invitations invitations_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY invitations_owner ON public.invitations USING (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)) WITH CHECK (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: agent_connectors isolasi_agent_connectors; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_agent_connectors ON public.agent_connectors USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: agent_goal_models isolasi_agent_goal_models; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_agent_goal_models ON public.agent_goal_models USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: agent_meta isolasi_agent_meta; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_agent_meta ON public.agent_meta USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: belief_versions isolasi_belief_versions; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_belief_versions ON public.belief_versions USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: block_requests isolasi_block_requests; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_block_requests ON public.block_requests USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: canary_runs isolasi_canary_runs; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_canary_runs ON public.canary_runs USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: cassettes isolasi_cassettes; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_cassettes ON public.cassettes USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: causal_hypotheses isolasi_causal_hypotheses; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_causal_hypotheses ON public.causal_hypotheses USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: conflict_records isolasi_conflict_records; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_conflict_records ON public.conflict_records USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: evidence isolasi_evidence; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_evidence ON public.evidence USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: false_reports isolasi_false_reports; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_false_reports ON public.false_reports USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: handoffs isolasi_handoffs; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_handoffs ON public.handoffs USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: listing_links isolasi_listing_links; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_listing_links ON public.listing_links USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: loop_corrections isolasi_loop_corrections; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_loop_corrections ON public.loop_corrections USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: loop_experiences isolasi_loop_experiences; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_loop_experiences ON public.loop_experiences USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: module_rows isolasi_module_rows; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_module_rows ON public.module_rows USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: module_tables isolasi_module_tables; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_module_tables ON public.module_tables USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: modules isolasi_modules; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_modules ON public.modules USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: monthly_credits isolasi_monthly_credits; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_monthly_credits ON public.monthly_credits USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: price_series isolasi_price_series; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_price_series ON public.price_series USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: procedure_uses isolasi_procedure_uses; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_procedure_uses ON public.procedure_uses USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: procedure_versions isolasi_procedure_versions; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_procedure_versions ON public.procedure_versions USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: push_subscriptions isolasi_push_subscriptions; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_push_subscriptions ON public.push_subscriptions USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: read_budgets isolasi_read_budgets; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_read_budgets ON public.read_budgets USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: shared_graph_private_records isolasi_shared_graph_private_records; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_shared_graph_private_records ON public.shared_graph_private_records USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: source_readings isolasi_source_readings; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_source_readings ON public.source_readings USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: tasks isolasi_tasks; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_tasks ON public.tasks USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: token_metrics isolasi_token_metrics; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_token_metrics ON public.token_metrics USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: triggers isolasi_triggers; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_triggers ON public.triggers USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: watchlist isolasi_watchlist; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_watchlist ON public.watchlist USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: workflow_approvals isolasi_workflow_approvals; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_workflow_approvals ON public.workflow_approvals USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: workflow_library isolasi_workflow_library; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_workflow_library ON public.workflow_library USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: workflow_runs isolasi_workflow_runs; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_workflow_runs ON public.workflow_runs USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: workflows isolasi_workflows; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_workflows ON public.workflows USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: world_beliefs isolasi_world_beliefs; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY isolasi_world_beliefs ON public.world_beliefs USING ((current_setting('app.current_user_id'::text, true) = (user_id)::text)) WITH CHECK ((current_setting('app.current_user_id'::text, true) = (user_id)::text));


--
-- Name: listing_links; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.listing_links ENABLE ROW LEVEL SECURITY;

--
-- Name: loop_corrections; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.loop_corrections ENABLE ROW LEVEL SECURITY;

--
-- Name: loop_experiences; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.loop_experiences ENABLE ROW LEVEL SECURITY;

--
-- Name: meeting_minutes; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.meeting_minutes ENABLE ROW LEVEL SECURITY;

--
-- Name: meeting_minutes meeting_minutes_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY meeting_minutes_insert ON public.meeting_minutes FOR INSERT TO puspa_app WITH CHECK ((public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid) AND (author_user_id = (current_setting('app.current_user_id'::text, true))::uuid)));


--
-- Name: meeting_minutes meeting_minutes_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY meeting_minutes_select ON public.meeting_minutes FOR SELECT TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: mockups; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.mockups ENABLE ROW LEVEL SECURITY;

--
-- Name: mockups mockups_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY mockups_owner ON public.mockups USING ((user_id = current_setting('app.current_user_id'::text, true))) WITH CHECK ((user_id = current_setting('app.current_user_id'::text, true)));


--
-- Name: module_rows; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.module_rows ENABLE ROW LEVEL SECURITY;

--
-- Name: module_tables; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.module_tables ENABLE ROW LEVEL SECURITY;

--
-- Name: modules; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.modules ENABLE ROW LEVEL SECURITY;

--
-- Name: monthly_credits; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.monthly_credits ENABLE ROW LEVEL SECURITY;

--
-- Name: onboarding; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.onboarding ENABLE ROW LEVEL SECURITY;

--
-- Name: onboarding onboarding_own_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY onboarding_own_own ON public.onboarding USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: slice_grants pemberian_irisan_own_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY pemberian_irisan_own_own ON public.slice_grants USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: prepared_payments; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.prepared_payments ENABLE ROW LEVEL SECURITY;

--
-- Name: prepared_payments prepared_payments_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY prepared_payments_insert ON public.prepared_payments FOR INSERT TO puspa_app WITH CHECK ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: prepared_payments prepared_payments_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY prepared_payments_select ON public.prepared_payments FOR SELECT TO puspa_app USING ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: prepared_payments prepared_payments_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY prepared_payments_update ON public.prepared_payments FOR UPDATE TO puspa_app USING ((user_id = (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: price_series; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.price_series ENABLE ROW LEVEL SECURITY;

--
-- Name: procedure_uses; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.procedure_uses ENABLE ROW LEVEL SECURITY;

--
-- Name: procedure_versions; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.procedure_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: procedures; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.procedures ENABLE ROW LEVEL SECURITY;

--
-- Name: profile_facts; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.profile_facts ENABLE ROW LEVEL SECURITY;

--
-- Name: procedures prosedur_own_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY prosedur_own_own ON public.procedures USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: push_subscriptions; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.push_subscriptions ENABLE ROW LEVEL SECURITY;

--
-- Name: read_budgets; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.read_budgets ENABLE ROW LEVEL SECURITY;

--
-- Name: canonical_products read_shared_canonical_products; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY read_shared_canonical_products ON public.canonical_products FOR SELECT USING ((current_setting('app.current_user_id'::text, true) <> ''::text));


--
-- Name: evidence_cache read_shared_evidence_cache; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY read_shared_evidence_cache ON public.evidence_cache FOR SELECT USING ((current_setting('app.current_user_id'::text, true) <> ''::text));


--
-- Name: room_channel_links; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.room_channel_links ENABLE ROW LEVEL SECURITY;

--
-- Name: room_channel_links room_channel_links_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_channel_links_delete ON public.room_channel_links FOR DELETE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_channel_links room_channel_links_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_channel_links_insert ON public.room_channel_links FOR INSERT TO puspa_app WITH CHECK (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_channel_links room_channel_links_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_channel_links_select ON public.room_channel_links FOR SELECT TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_invites; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.room_invites ENABLE ROW LEVEL SECURITY;

--
-- Name: room_invites room_invites_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_invites_delete ON public.room_invites FOR DELETE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_invites room_invites_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_invites_insert ON public.room_invites FOR INSERT TO puspa_app WITH CHECK (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_invites room_invites_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_invites_select ON public.room_invites FOR SELECT TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_invites room_invites_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_invites_update ON public.room_invites FOR UPDATE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_members; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.room_members ENABLE ROW LEVEL SECURITY;

--
-- Name: room_members room_members_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_members_delete ON public.room_members FOR DELETE TO puspa_app USING (((user_id = (current_setting('app.current_user_id'::text, true))::uuid) OR public.is_room_owner(room_id, (current_setting('app.current_user_id'::text, true))::uuid)));


--
-- Name: room_members room_members_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_members_insert ON public.room_members FOR INSERT TO puspa_app WITH CHECK (public.is_room_owner(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_members room_members_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_members_select ON public.room_members FOR SELECT TO puspa_app USING (((user_id = (current_setting('app.current_user_id'::text, true))::uuid) OR public.is_room_owner(room_id, (current_setting('app.current_user_id'::text, true))::uuid)));


--
-- Name: room_members room_members_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_members_update ON public.room_members FOR UPDATE TO puspa_app USING (public.is_room_owner(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_memories; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.room_memories ENABLE ROW LEVEL SECURITY;

--
-- Name: room_memories room_memories_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_memories_delete ON public.room_memories FOR DELETE TO puspa_app USING (((author_user_id = (current_setting('app.current_user_id'::text, true))::uuid) OR public.is_room_owner(room_id, (current_setting('app.current_user_id'::text, true))::uuid)));


--
-- Name: room_memories room_memories_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_memories_insert ON public.room_memories FOR INSERT TO puspa_app WITH CHECK ((public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid) AND (author_user_id = (current_setting('app.current_user_id'::text, true))::uuid)));


--
-- Name: room_memories room_memories_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_memories_select ON public.room_memories FOR SELECT TO puspa_app USING ((public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid) AND ((visibility = 'room'::text) OR (author_user_id = (current_setting('app.current_user_id'::text, true))::uuid))));


--
-- Name: room_money_approvals; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.room_money_approvals ENABLE ROW LEVEL SECURITY;

--
-- Name: room_money_approvals room_money_approvals_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_money_approvals_insert ON public.room_money_approvals FOR INSERT TO puspa_app WITH CHECK ((public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid) AND (approver_user_id = (current_setting('app.current_user_id'::text, true))::uuid)));


--
-- Name: room_money_approvals room_money_approvals_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_money_approvals_select ON public.room_money_approvals FOR SELECT TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_research_links; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.room_research_links ENABLE ROW LEVEL SECURITY;

--
-- Name: room_research_links room_research_links_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_research_links_delete ON public.room_research_links FOR DELETE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_research_links room_research_links_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_research_links_insert ON public.room_research_links FOR INSERT TO puspa_app WITH CHECK (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_research_links room_research_links_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_research_links_select ON public.room_research_links FOR SELECT TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_splits; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.room_splits ENABLE ROW LEVEL SECURITY;

--
-- Name: room_splits room_splits_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_splits_delete ON public.room_splits FOR DELETE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_splits room_splits_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_splits_insert ON public.room_splits FOR INSERT TO puspa_app WITH CHECK (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_splits room_splits_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_splits_select ON public.room_splits FOR SELECT TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_splits room_splits_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_splits_update ON public.room_splits FOR UPDATE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_tasks; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.room_tasks ENABLE ROW LEVEL SECURITY;

--
-- Name: room_tasks room_tasks_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_tasks_delete ON public.room_tasks FOR DELETE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_tasks room_tasks_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_tasks_insert ON public.room_tasks FOR INSERT TO puspa_app WITH CHECK (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_tasks room_tasks_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_tasks_select ON public.room_tasks FOR SELECT TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: room_tasks room_tasks_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY room_tasks_update ON public.room_tasks FOR UPDATE TO puspa_app USING (public.is_room_member(room_id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: rooms; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.rooms ENABLE ROW LEVEL SECURITY;

--
-- Name: rooms rooms_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY rooms_delete ON public.rooms FOR DELETE TO puspa_app USING (public.is_room_owner(id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: rooms rooms_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY rooms_select ON public.rooms FOR SELECT TO puspa_app USING (public.is_room_member(id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: rooms rooms_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY rooms_update ON public.rooms FOR UPDATE TO puspa_app USING (public.is_room_owner(id, (current_setting('app.current_user_id'::text, true))::uuid));


--
-- Name: sessions; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: sessions sessions_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY sessions_own ON public.sessions USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: shared_graph_private_records; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.shared_graph_private_records ENABLE ROW LEVEL SECURITY;

--
-- Name: slice_grants; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.slice_grants ENABLE ROW LEVEL SECURITY;

--
-- Name: source_readings; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.source_readings ENABLE ROW LEVEL SECURITY;

--
-- Name: split_payments; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.split_payments ENABLE ROW LEVEL SECURITY;

--
-- Name: split_payments split_payments_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY split_payments_insert ON public.split_payments FOR INSERT TO puspa_app WITH CHECK ((EXISTS ( SELECT 1
   FROM public.room_splits s
  WHERE ((s.id = split_payments.split_id) AND public.is_room_member(s.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: split_payments split_payments_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY split_payments_select ON public.split_payments FOR SELECT TO puspa_app USING ((EXISTS ( SELECT 1
   FROM public.room_splits s
  WHERE ((s.id = split_payments.split_id) AND public.is_room_member(s.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: split_settlements; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.split_settlements ENABLE ROW LEVEL SECURITY;

--
-- Name: split_settlements split_settlements_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY split_settlements_insert ON public.split_settlements FOR INSERT TO puspa_app WITH CHECK ((EXISTS ( SELECT 1
   FROM public.room_splits s
  WHERE ((s.id = split_settlements.split_id) AND public.is_room_member(s.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: split_settlements split_settlements_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY split_settlements_select ON public.split_settlements FOR SELECT TO puspa_app USING ((EXISTS ( SELECT 1
   FROM public.room_splits s
  WHERE ((s.id = split_settlements.split_id) AND public.is_room_member(s.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: split_settlements split_settlements_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY split_settlements_update ON public.split_settlements FOR UPDATE TO puspa_app USING ((EXISTS ( SELECT 1
   FROM public.room_splits s
  WHERE ((s.id = split_settlements.split_id) AND public.is_room_member(s.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: split_shares; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.split_shares ENABLE ROW LEVEL SECURITY;

--
-- Name: split_shares split_shares_delete; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY split_shares_delete ON public.split_shares FOR DELETE TO puspa_app USING ((EXISTS ( SELECT 1
   FROM public.room_splits s
  WHERE ((s.id = split_shares.split_id) AND public.is_room_member(s.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: split_shares split_shares_insert; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY split_shares_insert ON public.split_shares FOR INSERT TO puspa_app WITH CHECK ((EXISTS ( SELECT 1
   FROM public.room_splits s
  WHERE ((s.id = split_shares.split_id) AND public.is_room_member(s.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: split_shares split_shares_select; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY split_shares_select ON public.split_shares FOR SELECT TO puspa_app USING ((EXISTS ( SELECT 1
   FROM public.room_splits s
  WHERE ((s.id = split_shares.split_id) AND public.is_room_member(s.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: split_shares split_shares_update; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY split_shares_update ON public.split_shares FOR UPDATE TO puspa_app USING ((EXISTS ( SELECT 1
   FROM public.room_splits s
  WHERE ((s.id = split_shares.split_id) AND public.is_room_member(s.room_id, (current_setting('app.current_user_id'::text, true))::uuid)))));


--
-- Name: style_examples; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.style_examples ENABLE ROW LEVEL SECURITY;

--
-- Name: summaries; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.summaries ENABLE ROW LEVEL SECURITY;

--
-- Name: summaries summary_own_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY summary_own_own ON public.summaries USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: tasks; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;

--
-- Name: team_members; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.team_members ENABLE ROW LEVEL SECURITY;

--
-- Name: team_members team_members_delete_owner_or_self; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY team_members_delete_owner_or_self ON public.team_members FOR DELETE USING ((((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)) OR public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)));


--
-- Name: team_members team_members_insert_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY team_members_insert_owner ON public.team_members FOR INSERT WITH CHECK (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: team_members team_members_update_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY team_members_update_owner ON public.team_members FOR UPDATE USING (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)) WITH CHECK (public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: team_members team_members_view_own_or_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY team_members_view_own_or_owner ON public.team_members FOR SELECT USING ((((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)) OR public.is_team_owner(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)));


--
-- Name: teams; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;

--
-- Name: teams teams_delete_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY teams_delete_owner ON public.teams FOR DELETE USING (public.is_team_owner(id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: teams teams_update_owner; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY teams_update_owner ON public.teams FOR UPDATE USING (public.is_team_owner(id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)) WITH CHECK (public.is_team_owner(id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: teams teams_view_member; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY teams_view_member ON public.teams FOR SELECT USING (public.is_team_member(id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: token_metrics; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.token_metrics ENABLE ROW LEVEL SECURITY;

--
-- Name: triggers; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.triggers ENABLE ROW LEVEL SECURITY;

--
-- Name: used_email_links; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.used_email_links ENABLE ROW LEVEL SECURITY;

--
-- Name: used_email_links used_email_links_deny_app; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY used_email_links_deny_app ON public.used_email_links USING (false) WITH CHECK (false);


--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

--
-- Name: users users_delete_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY users_delete_own ON public.users FOR DELETE USING (((id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: users users_register_or_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY users_register_or_own ON public.users FOR INSERT WITH CHECK (((COALESCE(current_setting('app.current_user_id'::text, true), ''::text) = ''::text) OR ((id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))));


--
-- Name: users users_update_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY users_update_own ON public.users FOR UPDATE USING (((id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: users users_view_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY users_view_own ON public.users FOR SELECT USING (((id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: vault; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.vault ENABLE ROW LEVEL SECURITY;

--
-- Name: vault vault_own; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY vault_own ON public.vault USING (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text))) WITH CHECK (((user_id)::text = COALESCE(current_setting('app.current_user_id'::text, true), ''::text)));


--
-- Name: watchlist; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.watchlist ENABLE ROW LEVEL SECURITY;

--
-- Name: workflow_approvals; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.workflow_approvals ENABLE ROW LEVEL SECURITY;

--
-- Name: workflow_library; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.workflow_library ENABLE ROW LEVEL SECURITY;

--
-- Name: workflow_runs; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.workflow_runs ENABLE ROW LEVEL SECURITY;

--
-- Name: workflows; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.workflows ENABLE ROW LEVEL SECURITY;

--
-- Name: workspace_moves; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.workspace_moves ENABLE ROW LEVEL SECURITY;

--
-- Name: workspace_moves workspace_moves_member; Type: POLICY; Schema: public; Owner: root
--

CREATE POLICY workspace_moves_member ON public.workspace_moves USING (public.is_team_member(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)) WITH CHECK (public.is_team_member(team_id, (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid));


--
-- Name: world_beliefs; Type: ROW SECURITY; Schema: public; Owner: root
--

ALTER TABLE public.world_beliefs ENABLE ROW LEVEL SECURITY;

--
-- Name: TABLE agent_revocations; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT ON TABLE public.agent_revocations TO puspa_app;


--
-- Name: TABLE bot_access_grants; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.bot_access_grants TO puspa_app;


--
-- Name: TABLE bot_access_requests; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.bot_access_requests TO puspa_app;


--
-- Name: TABLE data_consents; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.data_consents TO puspa_app;


--
-- Name: TABLE delegated_task_steps; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.delegated_task_steps TO puspa_app;


--
-- Name: TABLE delegated_tasks; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.delegated_tasks TO puspa_app;


--
-- Name: TABLE draft_approvals; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.draft_approvals TO puspa_app;


--
-- Name: TABLE drafts; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.drafts TO puspa_app;


--
-- Name: TABLE embedding_canary_baselines; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.embedding_canary_baselines TO puspa_app;


--
-- Name: TABLE embedding_reembed_jobs; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.embedding_reembed_jobs TO puspa_app;


--
-- Name: TABLE embedding_state; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.embedding_state TO puspa_app;


--
-- Name: TABLE entitlements; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.entitlements TO puspa_app;


--
-- Name: TABLE episodic; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.episodic TO puspa_app;


--
-- Name: TABLE events; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT ON TABLE public.events TO puspa_app;


--
-- Name: TABLE invitations; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.invitations TO puspa_app;


--
-- Name: TABLE kill_switch; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT ON TABLE public.kill_switch TO puspa_app;


--
-- Name: TABLE meeting_minutes; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT ON TABLE public.meeting_minutes TO puspa_app;


--
-- Name: TABLE mockups; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.mockups TO puspa_app;


--
-- Name: TABLE onboarding; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.onboarding TO puspa_app;


--
-- Name: TABLE prepared_payments; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,UPDATE ON TABLE public.prepared_payments TO puspa_app;


--
-- Name: TABLE procedures; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.procedures TO puspa_app;


--
-- Name: TABLE profile_facts; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.profile_facts TO puspa_app;


--
-- Name: TABLE room_channel_links; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.room_channel_links TO puspa_app;


--
-- Name: TABLE room_invites; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.room_invites TO puspa_app;


--
-- Name: TABLE room_members; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.room_members TO puspa_app;


--
-- Name: TABLE room_memories; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.room_memories TO puspa_app;


--
-- Name: TABLE room_money_approvals; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.room_money_approvals TO puspa_app;


--
-- Name: TABLE room_research_links; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.room_research_links TO puspa_app;


--
-- Name: TABLE room_splits; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.room_splits TO puspa_app;


--
-- Name: TABLE room_tasks; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.room_tasks TO puspa_app;


--
-- Name: TABLE rooms; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.rooms TO puspa_app;


--
-- Name: TABLE sessions; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.sessions TO puspa_app;


--
-- Name: TABLE slice_grants; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.slice_grants TO puspa_app;


--
-- Name: TABLE split_payments; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.split_payments TO puspa_app;


--
-- Name: TABLE split_settlements; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.split_settlements TO puspa_app;


--
-- Name: TABLE split_shares; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.split_shares TO puspa_app;


--
-- Name: TABLE style_examples; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.style_examples TO puspa_app;


--
-- Name: TABLE summaries; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.summaries TO puspa_app;


--
-- Name: TABLE team_members; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.team_members TO puspa_app;


--
-- Name: TABLE teams; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.teams TO puspa_app;


--
-- Name: TABLE token_revocations; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT ON TABLE public.token_revocations TO puspa_app;


--
-- Name: TABLE used_email_links; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.used_email_links TO puspa_app;


--
-- Name: TABLE users; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.users TO puspa_app;


--
-- Name: TABLE vault; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.vault TO puspa_app;


--
-- Name: TABLE workspace_moves; Type: ACL; Schema: public; Owner: root
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.workspace_moves TO puspa_app;


--
-- PostgreSQL database dump complete
--

\unrestrict 08RSOKFLqDPMddy0sffqJiZ6ALpkpwxZVhPMvkzcpx7eUq9SSc1vG7RqDweRfBR

