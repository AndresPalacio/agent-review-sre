"""Import target selecting local SQLite unless ``DATABASE_URL`` requests Postgres."""

from fin_graph.server.factory import RuntimeProfile, create_app

app = create_app(RuntimeProfile.from_env())
