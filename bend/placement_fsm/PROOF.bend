import Base
import ./placement.bend as S
import ./LAWS.bend as Laws

def Laws.unavailable_unknown(agent, scoped):
  Unit{}

def Laws.unscoped_agent_violation():
  Unit{}

def Laws.scoped_agent_pass():
  Unit{}

def Laws.non_agent_pass(scoped):
  Unit{}
