BEGIN;



-- Running upgrade 0005_return_import_status -> 0006_macro_references



CREATE TABLE reference_series (

    id SERIAL NOT NULL,

    series_code VARCHAR(80) NOT NULL,

    source VARCHAR(80) NOT NULL,

    frequency VARCHAR(20) NOT NULL,

    currency VARCHAR(3) NOT NULL,

    units VARCHAR(40) NOT NULL,

    return_series_id INTEGER,

    start_date DATE NOT NULL,

    end_date DATE NOT NULL,

    observation_count INTEGER NOT NULL,

    last_updated_at TIMESTAMP WITH TIME ZONE NOT NULL,

    data_version VARCHAR(64) NOT NULL,

    source_metadata JSON NOT NULL,

    CONSTRAINT pk_reference_series PRIMARY KEY (id),

    CONSTRAINT uq_reference_series_series_code UNIQUE (series_code),

    FOREIGN KEY(return_series_id) REFERENCES return_series (id)

);



CREATE TABLE reference_observations (

    id SERIAL NOT NULL,

    series_id INTEGER NOT NULL,

    month DATE NOT NULL,

    value NUMERIC(38, 18) NOT NULL,

    status VARCHAR(40) NOT NULL,

    source_snapshot VARCHAR(64) NOT NULL,

    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,

    CONSTRAINT pk_reference_observations PRIMARY KEY (id),

    CONSTRAINT uq_reference_observations_series_id UNIQUE (series_id, month),

    FOREIGN KEY(series_id) REFERENCES reference_series (id)

);



CREATE INDEX ix_reference_observations_series_id ON reference_observations (series_id);



CREATE TABLE reference_revisions (

    id SERIAL NOT NULL,

    series_id INTEGER NOT NULL,

    month DATE NOT NULL,

    old_value NUMERIC(38, 18) NOT NULL,

    new_value NUMERIC(38, 18) NOT NULL,

    old_status VARCHAR(40) NOT NULL,

    new_status VARCHAR(40) NOT NULL,

    source_snapshot VARCHAR(64) NOT NULL,

    detected_at TIMESTAMP WITH TIME ZONE NOT NULL,

    applied_at TIMESTAMP WITH TIME ZONE,

    CONSTRAINT pk_reference_revisions PRIMARY KEY (id),

    FOREIGN KEY(series_id) REFERENCES reference_series (id)

);



UPDATE alembic_version SET version_num='0006_macro_references' WHERE alembic_version.version_num = '0005_return_import_status';



COMMIT;
