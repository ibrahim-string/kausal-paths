from typing import Optional
import graphene
from graphql import GraphQLResolveInfo
from graphql.error import GraphQLError
from wagtail.core.rich_text import expand_db_html
from nodes.models import InstanceConfig, NodeConfig

from paths.graphql_helpers import GQLInfo, GQLInstanceInfo, ensure_instance
from .metric import Metric

from . import Node
from .actions import ActionNode, ActionEfficiencyPair, ActionGroup
from .constants import BASELINE_VALUE_COLUMN, FORECAST_COLUMN, IMPACT_COLUMN, VALUE_COLUMN, DecisionLevel
from .scenario import Scenario


class InstanceHostname(graphene.ObjectType):
    hostname = graphene.String()
    base_path = graphene.String()


class ActionGroupType(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    color = graphene.String(required=False)
    actions = graphene.List('nodes.schema.NodeType')

    @staticmethod
    def resolve_actions(root: ActionGroup, info: GQLInstanceInfo):
        context = info.context.instance.context
        return [act for act in context.get_actions() if act.group == root]


class InstanceType(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    owner = graphene.String()
    default_language = graphene.String()
    supported_languages = graphene.List(graphene.String)
    base_path = graphene.String()
    target_year = graphene.Int()
    reference_year = graphene.Int()
    minimum_historical_year = graphene.Int()
    maximum_historical_year = graphene.Int()

    hostname = graphene.Field(InstanceHostname, hostname=graphene.String())
    lead_title = graphene.String()
    lead_paragraph = graphene.String()
    theme_identifier = graphene.String()
    action_groups = graphene.List(ActionGroupType)

    def resolve_lead_title(root, info):
        obj = InstanceConfig.objects.filter(identifier=root.id).first()
        if obj is None:
            return None
        return obj.lead_title_i18n

    def resolve_lead_paragraph(root, info):
        obj = InstanceConfig.objects.filter(identifier=root.id).first()
        if obj is None:
            return None
        return obj.lead_paragraph_i18n

    def resolve_hostname(root, info, hostname):
        return InstanceConfig.objects.get(identifier=root.id)\
            .hostnames.filter(hostname__iexact=hostname).first()


class YearlyValue(graphene.ObjectType):
    year = graphene.Int()
    value = graphene.Float()


class ForecastMetricType(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    # output_node will be set if the node outputs multiple time-series
    output_node = graphene.Field(lambda: NodeType)
    unit = graphene.Field('paths.schema.UnitType')
    yearly_cumulative_unit = graphene.Field('paths.schema.UnitType')
    historical_values = graphene.List(YearlyValue, latest=graphene.Int(required=False))
    forecast_values = graphene.List(YearlyValue)
    cumulative_forecast_value = graphene.Float()
    baseline_forecast_values = graphene.List(YearlyValue)

    @staticmethod
    def resolve_historical_values(root: Metric, info, latest: Optional[int] = None):
        ret = root.get_historical_values()
        if latest:
            if latest >= len(ret):
                return ret
            return ret[-latest:]
        return ret

    @staticmethod
    def resolve_forecast_values(root: Metric, info):
        return root.get_forecast_values()

    @staticmethod
    def resolve_baseline_forecast_values(root: Metric, info):
        return root.get_baseline_forecast_values()

    @staticmethod
    def resolve_cumulative_forecast_value(root: Metric, info):
        return root.get_cumulative_forecast_value()


ActionDecisionLevel = graphene.Enum.from_enum(DecisionLevel)


class NodeType(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    color = graphene.String()
    order = graphene.Int(required=False)
    unit = graphene.Field('paths.schema.UnitType')
    quantity = graphene.String()
    target_year_goal = graphene.Float()
    is_action = graphene.Boolean()
    decision_level = graphene.Field(ActionDecisionLevel)
    input_nodes = graphene.List(lambda: NodeType)
    output_nodes = graphene.List(lambda: NodeType)
    downstream_nodes = graphene.List(lambda: NodeType)
    upstream_nodes = graphene.List(lambda: NodeType, same_unit=graphene.Boolean(), same_quantity=graphene.Boolean())
    upstream_actions = graphene.List(lambda: NodeType)
    group = graphene.Field(ActionGroupType, required=False)

    # TODO: Many nodes will output multiple time series. Remove metric
    # and handle a single-metric node as a special case in the UI??
    metric = graphene.Field(ForecastMetricType)
    output_metrics = graphene.List(ForecastMetricType)
    # If resolving through `descendant_nodes`, `impact_metric` will be
    # by default be calculated from the ancestor node.
    impact_metric = graphene.Field(ForecastMetricType, target_node_id=graphene.ID(required=False))

    # TODO: input_datasets, baseline_values, context
    parameters = graphene.List('params.schema.ParameterInterface')

    # These are potentially plucked from nodes.models.NodeConfig
    short_description = graphene.String()
    description = graphene.String()

    @staticmethod
    def resolve_color(root: Node, info):
        if root.color:
            return root.color
        if root.quantity == 'emissions':
            for parent in root.output_nodes:
                if parent.color:
                    root.color = parent.color
                    return root.color

    @staticmethod
    def resolve_is_action(root: Node, info):
        return isinstance(root, ActionNode)

    @staticmethod
    def resolve_downstream_nodes(root: Node, info: GQLInstanceInfo):
        info.context._upstream_node = root
        return root.get_downstream_nodes()

    @staticmethod
    def resolve_upstream_nodes(root: Node, info: GQLInstanceInfo, same_unit: bool = False, same_quantity: bool = False):
        def filter_nodes(node):
            if same_unit:
                if root.unit != node.unit:
                    return False
            if same_quantity:
                if root.quantity != node.quantity:
                    return False
            return True
        return root.get_upstream_nodes(filter=filter_nodes)

    @staticmethod
    def resolve_upstream_actions(root: Node, info: GQLInstanceInfo):
        return root.get_upstream_nodes(filter=lambda x: isinstance(x, ActionNode))

    @staticmethod
    def resolve_metric(root: Node, info):
        return Metric.from_node(root)

    @staticmethod
    def resolve_impact_metric(root: Node, info: GraphQLResolveInfo, target_node_id: str = None):
        context = info.context.instance.context
        upstream_node = getattr(info.context, '_upstream_node', None)
        if target_node_id is not None:
            if target_node_id not in context.nodes:
                raise GraphQLError("Node %s not found" % target_node_id, info.field_nodes)
            source_node = root
            target_node = context.get_node(target_node_id)
        elif upstream_node is not None:
            source_node = upstream_node
            target_node = root
        else:
            # FIXME: Determine a "default" target node from instance
            source_node = root
            target_node = context.get_node('net_emissions')

        if not isinstance(source_node, ActionNode):
            return None

        df = source_node.compute_impact(target_node)
        df = df[[IMPACT_COLUMN, FORECAST_COLUMN]]
        df = df.rename(columns={IMPACT_COLUMN: VALUE_COLUMN})
        metric = Metric(
            id='%s-%s-impact' % (root.id, target_node.id), name='Impact', df=df,
            unit=target_node.unit
        )
        return metric

    def resolve_group(root: Node, info: GQLInstanceInfo):
        if not isinstance(root, ActionNode):
            return None
        return root.group

    def resolve_parameters(root, info):
        return [param for param in root.parameters.values() if param.is_customizable]

    def resolve_short_description(root, info: GQLInstanceInfo) -> Optional[str]:
        obj: NodeConfig = (NodeConfig.objects
                           .filter(instance__identifier=info.context.instance.id, identifier=root.id)
                           .first())
        if obj is not None and obj.short_description_i18n:
            return expand_db_html(obj.short_description_i18n)
        if root.description:
            return '<p>%s</p>' % root.description
        return None

    def resolve_description(root, info: GQLInstanceInfo) -> Optional[str]:
        obj = (NodeConfig.objects
               .filter(instance__identifier=info.context.instance.id, identifier=root.id)
               .first())
        if obj is None or not obj.description_i18n:
            return None
        return expand_db_html(obj.description_i18n)


class ScenarioType(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    is_active = graphene.Boolean()
    is_default = graphene.Boolean()

    @staticmethod
    def resolve_is_active(root: Scenario, info: GQLInfo):
        context = info.context.instance.context
        return context.active_scenario == root

    @staticmethod
    def resolve_is_default(root: Scenario, info: GQLInfo):
        return root.default


class ActionEfficiency(graphene.ObjectType):
    action = graphene.Field(NodeType)
    cost_values = graphene.List(YearlyValue)
    impact_values = graphene.List(YearlyValue)
    cumulative_efficiency = graphene.Float()
    cumulative_cost = graphene.Float()
    cumulative_impact = graphene.Float()


class ActionEfficiencyPairType(graphene.ObjectType):
    cost_node = graphene.Field(NodeType)
    impact_node = graphene.Field(NodeType)
    efficiency_unit = graphene.Field('paths.schema.UnitType')
    label = graphene.String()
    actions = graphene.List(ActionEfficiency)

    @staticmethod
    def resolve_actions(root: ActionEfficiencyPair, info: GQLInstanceInfo):
        all_aes = root.calculate(info.context.instance.context)
        out = []
        for ae in all_aes:
            sum_fields = ['cumulative_efficiency', 'cumulative_cost', 'cumulative_impact']
            d = dict(
                action=ae.action,
                cost_values=[YearlyValue(year, float(val)) for year, val in ae.df['Cost'].pint.m.iteritems()],
                impact_values=[YearlyValue(year, float(val)) for year, val in ae.df['Impact'].pint.m.iteritems()],
                **{f: float(getattr(ae, f).m) for f in sum_fields},
            )
            out.append(d)
        return out

    @staticmethod
    def resolve_efficiency_unit(root: ActionEfficiencyPair, info: GQLInstanceInfo):
        return root.unit


class Query(graphene.ObjectType):
    instance = graphene.Field(InstanceType)
    nodes = graphene.List(NodeType)
    node = graphene.Field(
        NodeType, id=graphene.ID(required=True)
    )
    actions = graphene.List(NodeType)
    action_efficiency_pairs = graphene.List(ActionEfficiencyPairType)
    scenarios = graphene.List(ScenarioType)
    scenario = graphene.Field(ScenarioType, id=graphene.ID(required=True))
    active_scenario = graphene.Field(ScenarioType)

    @ensure_instance
    def resolve_instance(root, info: GQLInstanceInfo):
        return info.context.instance

    @ensure_instance
    def resolve_scenario(root, info: GQLInstanceInfo, id):
        context = info.context.instance.context
        return context.get_scenario(id)

    @ensure_instance
    def resolve_active_scenario(root, info: GQLInstanceInfo):
        context = info.context.instance.context
        return context.active_scenario

    @ensure_instance
    def resolve_scenarios(root, info: GQLInstanceInfo):
        context = info.context.instance.context
        return list(context.scenarios.values())

    @ensure_instance
    def resolve_node(root, info: GQLInstanceInfo, id: str):
        instance = info.context.instance
        nodes = instance.context.nodes
        if id.isnumeric():
            for node in nodes.values():
                if node.database_id is not None and node.database_id == int(id):
                    return node
            return None

        return instance.context.nodes.get(id)

    @ensure_instance
    def resolve_nodes(root, info: GQLInstanceInfo):
        instance = info.context.instance
        return instance.context.nodes.values()

    @ensure_instance
    def resolve_actions(root, info: GQLInstanceInfo):
        instance = info.context.instance
        return instance.context.get_actions()

    @ensure_instance
    def resolve_action_efficiency_pairs(root, info: GQLInstanceInfo):
        instance = info.context.instance
        ctx = instance.context
        return ctx.action_efficiency_pairs
