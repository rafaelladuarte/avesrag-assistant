CREATE DATABASE avesrag;

DO
$$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'username') THEN
      CREATE USERname "username" WITH PASSWORD 'password';
   END IF;
END
$$;

GRANT ALL PRIVILEGES ON DATABASE avesrag TO "username";

\c avesrag
GRANT ALL PRIVILEGES ON SCHEMA public TO "username";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "username";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "username";

-- Table: public.feedback_aves_rag

DROP TABLE IF EXISTS public.feedback_aves_rag;


CREATE TABLE IF NOT EXISTS public.feedback_aves_rag
(
    id integer NOT NULL DEFAULT nextval('feedback_aves_rag_id_seq'::regclass),
    especie character varying(255) COLLATE pg_catalog."default",
    nome_comum character varying(255) COLLATE pg_catalog."default",
    info_corretas boolean,
    especie_correta boolean,
    observacao text COLLATE pg_catalog."default",
    username_input text COLLATE pg_catalog."default",
    data_criacao timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT feedback_aves_rag_pkey PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.feedback_aves_rag
    OWNER to admin;