# Data Layer Architecture

## Overview

The Data Layer provides persistent storage, indexing, and retrieval capabilities for the multi-modal pipeline system. It implements a hybrid approach combining traditional databases, vector stores, object storage, and caching layers to handle diverse data types efficiently at scale.

## Storage Architecture

### Storage Tiers

```
┌─────────────────────────────────────────────────────────────┐
│                         Hot Storage                         │
│            In-Memory Cache (Redis) - < 1ms                  │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                        Warm Storage                         │
│         Vector DB (ChromaDB/Pinecone) - < 50ms             │
│          Metadata DB (PostgreSQL) - < 10ms                  │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                        Cold Storage                         │
│          Object Storage (S3/MinIO) - < 200ms               │
│            Archive (Glacier) - minutes to hours             │
└─────────────────────────────────────────────────────────────┘
```

### Data Models

```python
@dataclass
class DataObject:
    """Core data object representation"""
    object_id: str                      # UUID
    object_type: DataType              # text, image, audio, video, document
    content_hash: str                   # SHA-256 hash
    storage_location: StorageLocation   # S3 URI or local path
    metadata: Dict[str, Any]            # Flexible metadata
    created_at: datetime
    updated_at: datetime
    version: int
    
@dataclass
class Annotation:
    """Annotation attached to data objects"""
    annotation_id: str
    object_id: str
    annotator_id: str                  # User or system annotator
    annotation_type: str                # classification, extraction, summary
    content: Dict[str, Any]            # Annotation data
    confidence: float                   # 0.0 to 1.0
    created_at: datetime
    version: int
    
@dataclass
class Embedding:
    """Vector embedding for semantic search"""
    embedding_id: str
    object_id: str
    model_name: str                     # CLIP, BERT, etc.
    vector: List[float]                # Embedding vector
    dimensions: int
    metadata: Dict[str, Any]
    created_at: datetime
```

## Vector Database Integration

### Multi-Modal Embedding Strategy

```python
class EmbeddingPipeline:
    """Generate embeddings for different modalities"""
    
    def __init__(self):
        self.text_model = SentenceTransformer('all-mpnet-base-v2')
        self.image_model = CLIPModel.from_pretrained('openai/clip-vit-base-patch32')
        self.audio_model = AudioEmbedder('wav2vec2-base')
        
    def embed_text(self, text: str) -> np.ndarray:
        """Generate text embeddings"""
        return self.text_model.encode(text)
        
    def embed_image(self, image: PIL.Image) -> np.ndarray:
        """Generate image embeddings using CLIP"""
        inputs = self.image_processor(images=image, return_tensors="pt")
        outputs = self.image_model.get_image_features(**inputs)
        return outputs.cpu().numpy()
        
    def embed_audio(self, audio_path: str) -> np.ndarray:
        """Generate audio embeddings"""
        return self.audio_model.encode(audio_path)
        
    def embed_unified(self, data: Any, modality: str) -> np.ndarray:
        """Generate unified embeddings for cross-modal search"""
        if modality == "text":
            return self.embed_text(data)
        elif modality == "image":
            return self.embed_image(data)
        elif modality == "audio":
            return self.embed_audio(data)
        else:
            raise ValueError(f"Unsupported modality: {modality}")

### Vector Database Implementations

#### ChromaDB (Development/Small Scale)

```python
class ChromaDBAdapter:
    """ChromaDB adapter for vector storage"""
    
    def __init__(self, persist_directory: str = "./chroma_db"):
        import chromadb
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collections = {}
        
    def create_collection(self, name: str, metadata: Dict = None):
        """Create a new collection with metadata"""
        collection = self.client.create_collection(
            name=name,
            metadata=metadata or {},
            embedding_function=self.embedding_function
        )
        self.collections[name] = collection
        return collection
        
    def upsert_vectors(self, collection_name: str, 
                       ids: List[str], 
                       embeddings: List[List[float]], 
                       metadatas: List[Dict] = None):
        """Insert or update vectors in collection"""
        collection = self.collections[collection_name]
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas or [{}] * len(ids)
        )
        
    def search(self, collection_name: str, 
              query_embedding: List[float], 
              k: int = 10,
              filter: Dict = None) -> List[Dict]:
        """Search for similar vectors"""
        collection = self.collections[collection_name]
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=filter
        )
        return results
```

#### Pinecone (Cloud Production)

```python
class PineconeAdapter:
    """Pinecone adapter for cloud vector storage"""
    
    def __init__(self, api_key: str, environment: str):
        import pinecone
        pinecone.init(api_key=api_key, environment=environment)
        self.indexes = {}
        
    def create_index(self, name: str, dimension: int, metric: str = "cosine"):
        """Create a new Pinecone index"""
        import pinecone
        pinecone.create_index(
            name=name,
            dimension=dimension,
            metric=metric,
            pods=1,
            pod_type="p1.x1"
        )
        self.indexes[name] = pinecone.Index(name)
        
    def upsert_vectors(self, index_name: str, vectors: List[Tuple]):
        """Batch upsert vectors with metadata"""
        index = self.indexes[index_name]
        index.upsert(vectors=vectors, batch_size=100)
        
    def search(self, index_name: str, 
              query_vector: List[float], 
              top_k: int = 10,
              filter: Dict = None) -> Dict:
        """Search with optional metadata filtering"""
        index = self.indexes[index_name]
        return index.query(
            vector=query_vector,
            top_k=top_k,
            filter=filter,
            include_metadata=True
        )
```

#### Weaviate (Self-hosted Production)

```python
class WeaviateAdapter:
    """Weaviate adapter for self-hosted vector storage"""
    
    def __init__(self, url: str, api_key: str = None):
        import weaviate
        self.client = weaviate.Client(
            url=url,
            auth_client_secret=weaviate.AuthApiKey(api_key) if api_key else None
        )
        
    def create_schema(self, class_name: str, properties: List[Dict]):
        """Create Weaviate schema for data class"""
        schema = {
            "class": class_name,
            "properties": properties,
            "vectorizer": "text2vec-transformers"
        }
        self.client.schema.create_class(schema)
        
    def insert_data(self, class_name: str, data_object: Dict, vector: List[float]):
        """Insert data with custom vector"""
        self.client.data_object.create(
            data_object=data_object,
            class_name=class_name,
            vector=vector
        )
        
    def hybrid_search(self, class_name: str, 
                     query: str, 
                     alpha: float = 0.5,
                     limit: int = 10) -> List[Dict]:
        """Hybrid search combining vector and keyword search"""
        return (
            self.client.query
            .get(class_name, ["*"])
            .with_hybrid(query=query, alpha=alpha)
            .with_limit(limit)
            .do()
        )
```

## Metadata Database

### PostgreSQL Schema

```sql
-- Core tables
CREATE TABLE data_objects (
    object_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type VARCHAR(50) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    storage_location TEXT NOT NULL,
    file_size_bytes BIGINT,
    mime_type VARCHAR(100),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    version INTEGER DEFAULT 1,
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE TABLE annotations (
    annotation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_id UUID REFERENCES data_objects(object_id) ON DELETE CASCADE,
    annotator_id VARCHAR(100) NOT NULL,
    annotation_type VARCHAR(50) NOT NULL,
    content JSONB NOT NULL,
    confidence FLOAT CHECK (confidence >= 0 AND confidence <= 1),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    version INTEGER DEFAULT 1
);

CREATE TABLE embeddings (
    embedding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_id UUID REFERENCES data_objects(object_id) ON DELETE CASCADE,
    model_name VARCHAR(100) NOT NULL,
    dimensions INTEGER NOT NULL,
    vector_db_id VARCHAR(100),  -- ID in vector database
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE aggregations (
    aggregation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregation_type VARCHAR(50) NOT NULL,
    source_objects JSONB NOT NULL,  -- Array of object IDs
    result JSONB NOT NULL,
    created_by VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX idx_objects_type ON data_objects(object_type);
CREATE INDEX idx_objects_created ON data_objects(created_at DESC);
CREATE INDEX idx_objects_metadata ON data_objects USING GIN(metadata);
CREATE INDEX idx_annotations_object ON annotations(object_id);
CREATE INDEX idx_annotations_type ON annotations(annotation_type);
CREATE INDEX idx_embeddings_object ON embeddings(object_id);
CREATE INDEX idx_embeddings_model ON embeddings(model_name);
```

### Database Access Layer

```python
class DatabaseManager:
    """PostgreSQL database manager"""
    
    def __init__(self, connection_string: str):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        
        self.engine = create_engine(connection_string)
        self.Session = sessionmaker(bind=self.engine)
        
    def insert_data_object(self, obj: DataObject) -> str:
        """Insert new data object"""
        with self.Session() as session:
            result = session.execute(
                """
                INSERT INTO data_objects 
                (object_type, content_hash, storage_location, metadata)
                VALUES (:type, :hash, :location, :metadata)
                RETURNING object_id
                """,
                {
                    "type": obj.object_type,
                    "hash": obj.content_hash,
                    "location": obj.storage_location,
                    "metadata": json.dumps(obj.metadata)
                }
            )
            session.commit()
            return result.scalar()
            
    def get_objects_by_filter(self, filters: Dict) -> List[DataObject]:
        """Query objects with JSON filtering"""
        with self.Session() as session:
            query = "SELECT * FROM data_objects WHERE 1=1"
            params = {}
            
            if "object_type" in filters:
                query += " AND object_type = :type"
                params["type"] = filters["object_type"]
                
            if "metadata" in filters:
                for key, value in filters["metadata"].items():
                    query += f" AND metadata->'{key}' = :meta_{key}"
                    params[f"meta_{key}"] = json.dumps(value)
                    
            results = session.execute(query, params).fetchall()
            return [DataObject(**row) for row in results]
```

## Object Storage

### S3/MinIO Integration

```python
class ObjectStorageManager:
    """Manage binary object storage in S3 or MinIO"""
    
    def __init__(self, endpoint_url: str = None, 
                 access_key: str = None,
                 secret_key: str = None,
                 bucket_name: str = "pipeline-data"):
        import boto3
        
        self.s3_client = boto3.client(
            's3',
            endpoint_url=endpoint_url,  # MinIO endpoint or None for AWS
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key
        )
        self.bucket_name = bucket_name
        
    def upload_object(self, file_path: str, object_key: str, 
                     metadata: Dict = None) -> str:
        """Upload file to object storage"""
        extra_args = {}
        if metadata:
            extra_args['Metadata'] = {k: str(v) for k, v in metadata.items()}
            
        self.s3_client.upload_file(
            file_path, 
            self.bucket_name, 
            object_key,
            ExtraArgs=extra_args
        )
        
        return f"s3://{self.bucket_name}/{object_key}"
        
    def download_object(self, object_key: str, local_path: str):
        """Download object to local file"""
        self.s3_client.download_file(
            self.bucket_name,
            object_key,
            local_path
        )
        
    def generate_presigned_url(self, object_key: str, 
                              expiration: int = 3600) -> str:
        """Generate presigned URL for direct access"""
        return self.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket_name, 'Key': object_key},
            ExpiresIn=expiration
        )
        
    def list_objects(self, prefix: str = "", max_keys: int = 1000) -> List[Dict]:
        """List objects with prefix"""
        response = self.s3_client.list_objects_v2(
            Bucket=self.bucket_name,
            Prefix=prefix,
            MaxKeys=max_keys
        )
        return response.get('Contents', [])
```

### Storage Optimization

```python
class StorageOptimizer:
    """Optimize storage usage and costs"""
    
    def __init__(self, storage_manager: ObjectStorageManager):
        self.storage = storage_manager
        
    def compress_and_store(self, file_path: str, object_key: str) -> str:
        """Compress before storing"""
        import gzip
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix='.gz') as tmp:
            with open(file_path, 'rb') as f_in:
                with gzip.open(tmp.name, 'wb') as f_out:
                    f_out.writelines(f_in)
            
            return self.storage.upload_object(tmp.name, object_key + '.gz')
            
    def implement_lifecycle_policy(self):
        """Set up S3 lifecycle policies for cost optimization"""
        lifecycle_policy = {
            'Rules': [
                {
                    'Id': 'ArchiveOldData',
                    'Status': 'Enabled',
                    'Transitions': [
                        {
                            'Days': 30,
                            'StorageClass': 'STANDARD_IA'
                        },
                        {
                            'Days': 90,
                            'StorageClass': 'GLACIER'
                        }
                    ]
                },
                {
                    'Id': 'DeleteTempData',
                    'Status': 'Enabled',
                    'Prefix': 'temp/',
                    'Expiration': {'Days': 7}
                }
            ]
        }
        
        self.storage.s3_client.put_bucket_lifecycle_configuration(
            Bucket=self.storage.bucket_name,
            LifecycleConfiguration=lifecycle_policy
        )
```

## Cache Layer

### Redis Cache Implementation

```python
class CacheManager:
    """Multi-level cache management"""
    
    def __init__(self, redis_host: str = "localhost", redis_port: int = 6379):
        import redis
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            decode_responses=True
        )
        self.local_cache = {}  # L1 in-memory cache
        
    def get(self, key: str, level: str = "all") -> Optional[Any]:
        """Get from cache with level specification"""
        # Check L1 (local memory)
        if level in ["all", "memory"] and key in self.local_cache:
            return self.local_cache[key]
            
        # Check L2 (Redis)
        if level in ["all", "redis"]:
            value = self.redis_client.get(key)
            if value:
                # Populate L1
                self.local_cache[key] = json.loads(value)
                return self.local_cache[key]
                
        return None
        
    def set(self, key: str, value: Any, ttl: int = 3600):
        """Set in all cache levels"""
        # Set in L1
        self.local_cache[key] = value
        
        # Set in L2 with TTL
        self.redis_client.setex(
            key,
            ttl,
            json.dumps(value, default=str)
        )
        
    def invalidate(self, pattern: str):
        """Invalidate cache entries matching pattern"""
        # Clear from L1
        keys_to_remove = [k for k in self.local_cache if pattern in k]
        for key in keys_to_remove:
            del self.local_cache[key]
            
        # Clear from L2
        for key in self.redis_client.scan_iter(f"*{pattern}*"):
            self.redis_client.delete(key)
```

## Data Pipeline

### Ingestion Pipeline

```python
class DataIngestionPipeline:
    """Pipeline for ingesting multi-modal data"""
    
    def __init__(self, 
                 db_manager: DatabaseManager,
                 storage_manager: ObjectStorageManager,
                 vector_db: Any,
                 cache_manager: CacheManager):
        self.db = db_manager
        self.storage = storage_manager
        self.vector_db = vector_db
        self.cache = cache_manager
        self.embedding_pipeline = EmbeddingPipeline()
        
    async def ingest_data(self, 
                          file_path: str, 
                          data_type: str,
                          metadata: Dict = None) -> str:
        """Complete data ingestion flow"""
        
        # 1. Calculate content hash
        content_hash = self.calculate_hash(file_path)
        
        # 2. Check for duplicates
        if self.is_duplicate(content_hash):
            return self.get_existing_object_id(content_hash)
            
        # 3. Upload to object storage
        object_key = f"{data_type}/{content_hash[:8]}/{os.path.basename(file_path)}"
        storage_location = self.storage.upload_object(file_path, object_key)
        
        # 4. Create database entry
        data_object = DataObject(
            object_type=data_type,
            content_hash=content_hash,
            storage_location=storage_location,
            metadata=metadata or {}
        )
        object_id = self.db.insert_data_object(data_object)
        
        # 5. Generate embeddings
        if data_type in ["text", "image", "audio"]:
            embedding = await self.generate_embedding(file_path, data_type)
            self.store_embedding(object_id, embedding)
            
        # 6. Trigger async annotation
        await self.trigger_annotation(object_id, data_type)
        
        # 7. Update cache
        self.cache.set(f"object:{object_id}", data_object.dict())
        
        return object_id
        
    def calculate_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash of file content"""
        import hashlib
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
```

### Query Pipeline

```python
class QueryPipeline:
    """Pipeline for querying and retrieving data"""
    
    def __init__(self, 
                 db_manager: DatabaseManager,
                 vector_db: Any,
                 cache_manager: CacheManager):
        self.db = db_manager
        self.vector_db = vector_db
        self.cache = cache_manager
        self.embedding_pipeline = EmbeddingPipeline()
        
    async def semantic_search(self, 
                             query: str, 
                             modality: str = "text",
                             filters: Dict = None,
                             limit: int = 10) -> List[Dict]:
        """Perform semantic search across data"""
        
        # 1. Check cache
        cache_key = f"search:{hashlib.md5(f'{query}{filters}'.encode()).hexdigest()}"
        cached_result = self.cache.get(cache_key)
        if cached_result:
            return cached_result
            
        # 2. Generate query embedding
        query_embedding = self.embedding_pipeline.embed_unified(query, modality)
        
        # 3. Search vector database
        vector_results = self.vector_db.search(
            collection_name=modality,
            query_embedding=query_embedding.tolist(),
            k=limit * 2,  # Over-fetch for filtering
            filter=filters
        )
        
        # 4. Fetch metadata from database
        object_ids = [r['id'] for r in vector_results['matches']]
        objects = self.db.get_objects_by_ids(object_ids)
        
        # 5. Merge results
        results = []
        for vector_result, obj in zip(vector_results['matches'], objects):
            results.append({
                'object_id': obj.object_id,
                'score': vector_result['score'],
                'metadata': obj.metadata,
                'annotations': self.db.get_annotations(obj.object_id)
            })
            
        # 6. Cache results
        self.cache.set(cache_key, results, ttl=300)
        
        return results[:limit]
```

## Data Consistency

### Transaction Management

```python
class TransactionManager:
    """Manage distributed transactions across storage systems"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        
    @contextmanager
    def distributed_transaction(self):
        """Context manager for distributed transactions"""
        transaction_id = str(uuid.uuid4())
        rollback_actions = []
        
        try:
            # Begin database transaction
            with self.db.Session() as session:
                session.begin()
                
                # Yield control to caller
                yield TransactionContext(
                    transaction_id=transaction_id,
                    session=session,
                    rollback_actions=rollback_actions
                )
                
                # Commit if successful
                session.commit()
                
        except Exception as e:
            # Rollback database
            session.rollback()
            
            # Execute rollback actions
            for action in reversed(rollback_actions):
                try:
                    action()
                except Exception:
                    pass  # Log but continue rollback
                    
            raise e
```

## Monitoring and Metrics

```python
class DataLayerMetrics:
    """Collect metrics for data layer operations"""
    
    def __init__(self):
        from prometheus_client import Counter, Histogram, Gauge
        
        self.ingestion_counter = Counter(
            'data_ingestion_total',
            'Total data objects ingested',
            ['data_type', 'status']
        )
        
        self.query_latency = Histogram(
            'query_latency_seconds',
            'Query latency distribution',
            ['query_type']
        )
        
        self.storage_usage = Gauge(
            'storage_usage_bytes',
            'Storage usage in bytes',
            ['storage_type']
        )
        
        self.cache_hit_rate = Gauge(
            'cache_hit_rate',
            'Cache hit rate percentage'
        )
```

## Configuration

```yaml
# data_layer.yaml
database:
  postgres:
    host: localhost
    port: 5432
    database: pipeline_db
    username: pipeline_user
    password: ${DB_PASSWORD}
    pool_size: 20
    max_overflow: 40
    
vector_database:
  provider: chromadb  # chromadb, pinecone, weaviate
  chromadb:
    persist_directory: ./chroma_db
  pinecone:
    api_key: ${PINECONE_API_KEY}
    environment: us-west1-gcp
  weaviate:
    url: http://localhost:8080
    
object_storage:
  provider: minio  # minio, s3
  minio:
    endpoint: http://localhost:9000
    access_key: minioadmin
    secret_key: minioadmin
  s3:
    region: us-east-1
    bucket: pipeline-data
    
cache:
  redis:
    host: localhost
    port: 6379
    db: 0
    ttl_seconds: 3600
    
  local:
    max_size_mb: 1024
    eviction_policy: lru
```

## Next Steps

1. Configure [Plugin System Architecture](plugin-system-doc)
2. Implement [Multi-Modal Processor Component](multimodal-processor-doc)  
3. Set up [Vector Database Interface](vector-db-interface-doc)
4. Review [Storage Optimization Guide](storage-optimization-guide)