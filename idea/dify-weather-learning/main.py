if __name__ == "__main__":
    # SDK 0.8 discovers the Provider and Tool classes from manifest.yaml.
    # No manual register_provider call is required.
    from dify_plugin import DifyPluginEnv, Plugin

    Plugin(DifyPluginEnv()).run()
