"""Skill taxonomy: canonical name -> list of aliases (matched case-insensitively on word boundaries).

Used by both the resume parser and the job matcher, so a skill is spelled the same everywhere.
Add skills freely; aliases are regex fragments.
"""
import re

SKILLS: dict[str, list[str]] = {
    # Languages
    "Python": ["python"],
    "Java": [r"java(?!\s*script)"],
    "Scala": ["scala"],
    "Go": ["golang", r"go(?= lang)", r"go(?=\s*[,/|])", r"(?<=[,|/] )go"],
    "Rust": ["rust"],
    "C++": [r"c\+\+", "cpp"],
    "C#": [r"c#", r"\.net", "dotnet"],
    "JavaScript": ["javascript", r"\bjs\b", "es6"],
    "TypeScript": ["typescript", r"\bts\b"],
    "Kotlin": ["kotlin"],
    "Ruby": ["ruby"],
    "PHP": ["php"],
    "SQL": [r"\bsql\b", "t-sql", "pl/sql"],
    "R": ["r programming", "r language", "rstudio"],
    "Bash": ["bash", "shell scripting"],
    # Backend frameworks
    "Django": ["django"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi"],
    "Spring Boot": ["spring boot", "springboot", "spring"],
    "Node.js": [r"node\.?js", r"\bnode\b"],
    "Express": [r"express\.?js", r"\bexpress\b"],
    "Rails": ["rails", "ruby on rails"],
    "GraphQL": ["graphql"],
    "REST APIs": [r"rest(ful)? ?api", r"\brest\b"],
    "gRPC": ["grpc"],
    "Microservices": ["microservices?"],
    # Frontend
    "React": [r"react(\.?js)?"],
    "Angular": ["angular"],
    "Vue": [r"vue(\.?js)?"],
    "Next.js": [r"next\.?js"],
    "HTML/CSS": ["html", "css"],
    # Data engineering
    "Spark": ["spark", "pyspark"],
    "Hadoop": ["hadoop", "hdfs", "hive"],
    "Kafka": ["kafka"],
    "Airflow": ["airflow"],
    "dbt": [r"\bdbt\b"],
    "Flink": ["flink"],
    "ETL": ["etl", "elt", "data pipelines?"],
    "Snowflake": ["snowflake"],
    "BigQuery": ["bigquery", "big query"],
    "Redshift": ["redshift"],
    "Databricks": ["databricks"],
    "Data Warehousing": ["data warehous(e|ing)", "dimensional modell?ing"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    # Databases
    "PostgreSQL": ["postgres(ql)?"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongo(db)?"],
    "Redis": ["redis"],
    "Cassandra": ["cassandra"],
    "Elasticsearch": ["elasticsearch", "elastic search", "opensearch"],
    "DynamoDB": ["dynamodb"],
    # Cloud / infra
    "AWS": [r"\baws\b", "amazon web services", r"\bec2\b", r"\bs3\b", "lambda"],
    "GCP": [r"\bgcp\b", "google cloud"],
    "Azure": ["azure"],
    "Docker": ["docker", "containers?"],
    "Kubernetes": ["kubernetes", r"\bk8s\b", r"\beks\b", r"\bgke\b"],
    "Terraform": ["terraform"],
    "CI/CD": [r"ci/cd", r"\bci\b", "jenkins", "github actions", "gitlab ci"],
    "Linux": ["linux", "unix"],
    "Git": [r"\bgit\b", "github", "gitlab"],
    # ML / AI
    "Machine Learning": ["machine learning", r"\bml\b"],
    "Deep Learning": ["deep learning"],
    "PyTorch": ["pytorch"],
    "TensorFlow": ["tensorflow", "keras"],
    "scikit-learn": ["scikit-learn", "sklearn"],
    "NLP": [r"\bnlp\b", "natural language processing"],
    "LLMs": [r"\bllms?\b", "large language models?", r"gpt-?\d?", "openai", "claude", "llama", "mistral", "gemini"],
    "Generative AI": ["generative ai", "genai", "gen ai"],
    "RAG": [r"\brag\b", "retrieval[- ]augmented generation"],
    "AI Agents": ["ai agents?", "agentic( ai)?", "llm agents?", "autonomous agents?", "deepagents"],
    "Multi-Agent Systems": ["multi-agent( systems?)?", "agent orchestration", "crewai", "autogen"],
    "Tool Calling": ["tool calling", "function calling", "tool use"],
    "MCP": [r"\bmcp\b", "model context protocol"],
    "LangChain": ["langchain"],
    "LangGraph": ["langgraph"],
    "LlamaIndex": ["llamaindex", "llama index"],
    "Prompt Engineering": ["prompt engineering", "prompt design"],
    "Embeddings": ["embeddings?"],
    "Vector Databases": ["vector (search|databases?|stores?|db)", "faiss", "pinecone", "weaviate", "chroma(db)?",
                          "milvus", "qdrant", "pgvector"],
    "Hugging Face": ["hugging ?face", "transformers"],
    "Fine-tuning": ["fine[- ]?tun(e|ing)", "lora", "peft", "rlhf"],
    "LLM Evaluation": ["rag evaluation", "llm eval(uation)?s?", "ragas", "evals"],
    "Salesforce": ["salesforce", "apex", "visualforce", "lightning web components", r"\blwc\b"],
    "SOAP": [r"\bsoap\b"],
    "MLOps": ["mlops", "mlflow", "kubeflow", "sagemaker"],
    # Analytics / BI
    "Tableau": ["tableau"],
    "Power BI": ["power ?bi"],
    "Excel": ["excel"],
    "Statistics": ["statistics", "statistical"],
    # Practices
    "System Design": ["system design", "distributed systems?"],
    "Data Structures & Algorithms": ["data structures", "algorithms", r"\bdsa\b"],
    "Agile": ["agile", "scrum"],
    "Testing": ["unit test(ing)?", "pytest", "junit", r"\btdd\b"],
}

_COMPILED = {
    name: re.compile("|".join(rf"(?<![\w+#.]){a}(?![\w+#])" for a in aliases), re.IGNORECASE)
    for name, aliases in SKILLS.items()
}


def extract_skills(text: str) -> list[str]:
    """Return canonical skill names found in text, in taxonomy order."""
    return [name for name, rx in _COMPILED.items() if rx.search(text)]


import hashlib as _h
import json as _j

# Changes whenever the taxonomy changes, so cached per-job skill lists get recomputed.
TAXONOMY_VERSION = _h.sha1(_j.dumps(SKILLS, sort_keys=True).encode()).hexdigest()[:12]
