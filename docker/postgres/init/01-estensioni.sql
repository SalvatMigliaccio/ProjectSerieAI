-- Le estensioni, create una volta sola alla nascita del volume.
--
-- PERCHE' QUI E NON IN UNA MIGRAZIONE. `CREATE EXTENSION` vuole privilegi di
-- superutente, che l'utente applicativo non deve avere: se stesse in Alembic,
-- o si darebbero privilegi di troppo all'applicazione, o la migrazione
-- fallirebbe in produzione proprio mentre tutto il resto funziona.
--
-- Gli script di initdb NON sono migrazioni: girano solo se il volume e' vuoto
-- e non tengono traccia di cosa hanno gia' fatto. Qui sta solo cio' che deve
-- esistere PRIMA della prima migrazione.

-- Email senza distinzione fra maiuscole e minuscole. Senza, `Mario@x.it` e
-- `mario@x.it` sono due account diversi e l'unicita' non protegge niente:
-- si scopre il giorno in cui qualcuno si registra due volte e non riesce piu'
-- a entrare perche' non ricorda quale grafia aveva usato.
CREATE EXTENSION IF NOT EXISTS citext;

-- Ricerca fuzzy sul testo (trigrammi). Serve al pannello di amministrazione
-- per cercare un utente per email parziale senza scansionare la tabella.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Ricerca semantica. NON e' usata in questa fase e non c'e' niente da
-- incorporare finche' i dati di partita stanno in parquet (ADR 0002). Sta qui
-- perche' aggiungere un'estensione dopo significa toccare un database che
-- intanto ha utenti veri, e perche' l'immagine ce l'ha gia' dentro.
CREATE EXTENSION IF NOT EXISTS vector;

-- UUID v7: ordinati per tempo, quindi come chiave primaria non frammentano
-- l'indice come fa v4. Postgres 17 non ha ancora `uuidv7()` nativa (arriva
-- con la 18), quindi la genera l'applicazione; questa resta per `gen_random_uuid`
-- e per le funzioni crittografiche dei token.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
