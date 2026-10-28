from neo4j import GraphDatabase

from config import (
    NEO4J_URI,
    NEO4J_USERNAME,
    NEO4J_PASSWORD,
    NEO4J_DATABASE,
    TOPICS,
    validate_neo4j_config,
)
from search import rebuild_abstract

def check_neo4j() -> str:
    # Periksa konfigurasi sebelum mencoba koneksi.
    if (
        not NEO4J_URI
        or not NEO4J_USERNAME
        or not NEO4J_PASSWORD
        or "URI_DARI_AURA" in NEO4J_URI
        or "ISI_PASSWORD" in NEO4J_PASSWORD
    ):
        return (
            "Neo4j belum dikonfigurasi dengan lengkap "
            "di file .env."
        )

    try:
        with GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
        ) as driver:

            # Memeriksa konektivitas dan autentikasi.
            driver.verify_connectivity()

            # Memastikan query berhasil dijalankan.
            records, _, _ = driver.execute_query(
                "RETURN 1 AS result",
                database_=NEO4J_DATABASE,
            )

            if records and records[0]["result"] == 1:
                return "Koneksi Neo4j berhasil."

            return "Neo4j terhubung, tetapi query gagal."

    except Exception as error:
        # Detail error disimpan di terminal untuk debugging.
        print(f"Neo4j error: {type(error).__name__}: {error}")

        return (
            "Koneksi Neo4j gagal. Periksa URI, username, "
            "password, nama database, status instance, "
            "dan koneksi internet."
        )

    _driver = None

_driver = None


def get_driver():
    """Satu driver dipakai ulang (driver memang dirancang long-lived)."""
    global _driver

    if _driver is None:
        validate_neo4j_config()
        _driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
        )

    return _driver

def get_driver():
    """Satu driver dipakai ulang (driver memang dirancang long-lived)."""
    global _driver

    if _driver is None:
        validate_neo4j_config()
        _driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
        )

    return _driver


def _run(query: str, **params):
    records, _, _ = get_driver().execute_query(
        query,
        parameters_=params,
        database_=NEO4J_DATABASE,
    )
    return records


def init_schema() -> None:
    """Buat constraint + node Topic. Aman dipanggil berkali-kali."""
    _run(
        "CREATE CONSTRAINT paper_id IF NOT EXISTS "
        "FOR (p:Paper) REQUIRE p.openalex_id IS UNIQUE"
    )
    _run(
        "CREATE CONSTRAINT topic_name IF NOT EXISTS "
        "FOR (t:Topic) REQUIRE t.name IS UNIQUE"
    )
    _run(
        "CREATE CONSTRAINT author_id IF NOT EXISTS "
        "FOR (a:Author) REQUIRE a.id IS UNIQUE"
    )

    for name, parent in TOPICS.items():
        _run("MERGE (:Topic {name: $name})", name=name)

        if parent:
            _run(
                "MATCH (c:Topic {name: $name}) "
                "MERGE (p:Topic {name: $parent}) "
                "MERGE (c)-[:SUBTOPIC_OF]->(p)",
                name=name,
                parent=parent,
            )


def save_paper(paper: dict, finding: str, topics: list[str]) -> bool:
    """Simpan paper. Return True kalau baru, False kalau sudah ada."""
    paper_id = paper.get("id")

    if not paper_id:
        raise ValueError("Paper tidak punya id OpenAlex")

    exists = _run(
        "MATCH (p:Paper {openalex_id: $id}) RETURN count(p) AS n",
        id=paper_id,
    )[0]["n"] > 0

    authors = [
        {
            "id": a["author"]["id"],
            "name": a["author"].get("display_name") or "",
        }
        for a in (paper.get("authorships") or [])
        if (a.get("author") or {}).get("id")
    ]

    _run(
        """
        MERGE (p:Paper {openalex_id: $id})
        ON CREATE SET p.saved_at = datetime()
        SET p.title = $title,
            p.year = $year,
            p.doi = $doi,
            p.cited_by = $cited,
            p.abstract = $abstract,
            p.finding = $finding
        CALL (p) {
            UNWIND $authors AS a
            MERGE (au:Author {id: a.id})
            SET au.name = a.name
            MERGE (au)-[:AUTHORED]->(p)
        }
        CALL (p) {
            UNWIND $topics AS t
            MATCH (tp:Topic {name: t})
            MERGE (p)-[:HAS_TOPIC]->(tp)
        }
        """,
        id=paper_id,
        title=paper.get("title") or "",
        year=paper.get("publication_year"),
        doi=paper.get("doi"),
        cited=paper.get("cited_by_count", 0),
        abstract=rebuild_abstract(paper.get("abstract_inverted_index")),
        finding=finding,
        authors=authors,
        topics=topics,
    )

    return not exists


def list_saved(topic: str | None = None, limit: int = 20) -> list[dict]:
    """Daftar paper tersimpan. Filter topik ikut menyertakan subtopiknya
    (filter 'AI' juga menampilkan paper 'Machine Learning')."""
    records = _run(
        """
        MATCH (p:Paper)
        WHERE $topic IS NULL OR EXISTS {
            MATCH (p)-[:HAS_TOPIC]->(x:Topic)
            WHERE toLower(x.name) = toLower($topic)
               OR EXISTS {
                    MATCH (x)-[:SUBTOPIC_OF*1..]->(r:Topic)
                    WHERE toLower(r.name) = toLower($topic)
               }
        }
        RETURN p.title AS title,
               p.year AS year,
               p.doi AS doi,
               p.finding AS finding,
               [(p)-[:HAS_TOPIC]->(t:Topic) | t.name] AS topics
        ORDER BY p.saved_at DESC
        LIMIT $limit
        """,
        topic=topic,
        limit=limit,
    )

    return [r.data() for r in records]

def list_saved_grouped() -> list[dict]:
    """Semua paper tersimpan, dikelompokkan per topik."""
    records = _run(
        """
        MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic)
        WITH t, p
        ORDER BY p.saved_at DESC
        WITH t, collect({
            title: p.title,
            year: p.year,
            doi: p.doi,
            finding: p.finding
        }) AS papers
        RETURN t.name AS topic, papers
        ORDER BY size(papers) DESC, topic
        """
    )

    return [r.data() for r in records]

def topic_counts() -> list[dict]:
    records = _run(
        """
        MATCH (t:Topic)
        OPTIONAL MATCH (p:Paper)-[:HAS_TOPIC]->(t)
        RETURN t.name AS topic, count(p) AS total
        ORDER BY total DESC, topic
        """
    )

    return [r.data() for r in records]
