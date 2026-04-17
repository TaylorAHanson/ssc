import os
import glob
import yaml
from app.db.session import get_lakebase_session
from app.db.data_contract import DataContractModel
import uuid
from datetime import datetime

def migrate():
    db = get_lakebase_session()
    ocds_dir = os.path.join(os.getcwd(), "ocds")
    if not os.path.exists(ocds_dir):
        print(f"Directory {ocds_dir} not found.")
        return

    yaml_files = glob.glob(os.path.join(ocds_dir, "*.yaml")) + glob.glob(os.path.join(ocds_dir, "*.yml"))
    print(f"Found {len(yaml_files)} YAML files to migrate.")

    for file_path in yaml_files:
        try:
            with open(file_path, "r") as f:
                content = f.read()
                dataset_def = yaml.safe_load(content)
                
            servers = dataset_def.get("servers", [])
            catalog = servers[0].get("catalog", "main") if servers else "main"
            schema = servers[0].get("schema", "default") if servers else "default"
            
            schemas = dataset_def.get("schema", [])
            first_schema = schemas[0] if schemas else {}
            physical_table = first_schema.get("physicalName", "unknown")
            
            dataset_id = f"{catalog}.{schema}.{physical_table}"
            
            # Check if it exists
            existing = db.query(DataContractModel).filter(DataContractModel.dataset_id == dataset_id).first()
            if not existing:
                model = DataContractModel(
                    id=str(uuid.uuid4()),
                    dataset_id=dataset_id,
                    yaml_content=content,
                    version=1,
                    is_active=True,
                    created_at=datetime.utcnow(),
                    created_by="system_migration"
                )
                db.add(model)
                print(f"Migrated {file_path} as {dataset_id}")
            else:
                print(f"Skipping {file_path}, already exists as {dataset_id}")

        except Exception as e:
            print(f"Failed to migrate {file_path}: {e}")

    db.commit()
    db.close()
    print("Migration complete.")

if __name__ == "__main__":
    # Ensure tables are created
    from app.db.session import get_engine
    from app.db.base import Base
    import app.db.data_contract # ensure model is registered
    Base.metadata.create_all(bind=get_engine())
    
    migrate()