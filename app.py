"""PawPal+ Streamlit Application

A pet care planning assistant that helps owners manage tasks for their pets.
"""

import streamlit as st
from datetime import date
from pawpal_system import Task, Pet, Owner, Scheduler
from pawpal_ai.agent import CarePlanAgent
from pawpal_ai.retriever import KnowledgeRetriever, default_knowledge_dir

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

st.title("🐾 PawPal+")
st.caption("Pet care schedules, with a care advisor that cites its notes and checks its own suggestions")


@st.cache_resource
def load_retriever():
    """Load the local care notes once per server process."""
    return KnowledgeRetriever(default_knowledge_dir())

# Initialize session state with Owner object for persistence
if "owner" not in st.session_state:
    st.session_state.owner = Owner("Pet Owner")
    st.session_state.scheduler = Scheduler(st.session_state.owner)

owner = st.session_state.owner
scheduler = st.session_state.scheduler

# Sidebar for owner info
with st.sidebar:
    st.header("👤 Owner Profile")
    new_owner_name = st.text_input("Your name", value=owner.name)
    if new_owner_name != owner.name:
        owner.name = new_owner_name
    
    st.divider()
    st.markdown(f"**Pets:** {len(owner.pets)}")
    total_tasks = len(owner.get_all_tasks())
    st.markdown(f"**Total Tasks:** {total_tasks}")

# Main tabs for navigation
tab1, tab2, tab3, tab4 = st.tabs(
    ["🐕 Manage Pets", "📋 Tasks", "📅 Today's Schedule", "🤖 Care Advisor"]
)

with tab1:
    st.subheader("Add a New Pet")
    
    col1, col2 = st.columns(2)
    with col1:
        pet_name = st.text_input("Pet name", key="new_pet_name")
        species = st.selectbox("Species", ["dog", "cat", "bird", "fish", "other"])
    with col2:
        breed = st.text_input("Breed (optional)")
        age = st.number_input("Age (years)", min_value=0, max_value=30, value=1)
    
    if st.button("Add Pet", type="primary"):
        if pet_name:
            if owner.get_pet(pet_name):
                st.error(f"A pet named '{pet_name}' already exists!")
            else:
                new_pet = Pet(name=pet_name, species=species, breed=breed, age=age)
                owner.add_pet(new_pet)
                st.success(f"Added {pet_name} the {species}!")
                st.rerun()
        else:
            st.warning("Please enter a pet name.")
    
    st.divider()
    st.subheader("Your Pets")
    
    if owner.pets:
        for pet in owner.pets:
            with st.expander(f"🐾 {pet.name} ({pet.species})", expanded=False):
                st.markdown(f"**Breed:** {pet.breed or 'Unknown'}")
                st.markdown(f"**Age:** {pet.age} years")
                st.markdown(f"**Tasks:** {len(pet.tasks)}")
                
                if st.button(f"Remove {pet.name}", key=f"remove_{pet.name}"):
                    owner.remove_pet(pet.name)
                    st.rerun()
    else:
        st.info("No pets added yet. Add one above!")

with tab2:
    st.subheader("Schedule a Task")
    
    if not owner.pets:
        st.warning("Add a pet first before scheduling tasks!")
    else:
        pet_names = [p.name for p in owner.pets]
        selected_pet = st.selectbox("Select pet", pet_names)
        
        col1, col2, col3 = st.columns(3)
        with col1:
            task_desc = st.text_input("Task description", value="Morning walk")
            task_time = st.time_input("Scheduled time")
        with col2:
            duration = st.number_input("Duration (min)", min_value=1, max_value=240, value=30)
            priority = st.selectbox("Priority", ["low", "medium", "high"], index=2)
        with col3:
            frequency = st.selectbox("Frequency", ["once", "daily", "weekly"])
        
        if st.button("Add Task", type="primary"):
            pet = owner.get_pet(selected_pet)
            if pet:
                time_str = task_time.strftime("%H:%M")
                new_task = Task(
                    description=task_desc,
                    scheduled_time=time_str,
                    duration_minutes=duration,
                    priority=priority,
                    frequency=frequency,
                    scheduled_date=date.today(),
                )
                pet.add_task(new_task)
                st.success(f"Added '{task_desc}' for {selected_pet}!")
                st.rerun()
        
        st.divider()
        st.subheader("All Tasks")
        
        all_tasks = owner.get_all_tasks()
        if all_tasks:
            for pet in owner.pets:
                if pet.tasks:
                    st.markdown(f"**{pet.name}'s Tasks:**")
                    for task in pet.tasks:
                        status = "✅" if task.is_complete else "⬜"
                        priority_color = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(task.priority, "⚪")
                        freq_badge = f" [{task.frequency}]" if task.frequency != "once" else ""
                        
                        col1, col2, col3 = st.columns([3, 1, 1])
                        with col1:
                            st.markdown(f"{status} {task.scheduled_time} - {task.description} ({task.duration_minutes}min) {priority_color}{freq_badge}")
                        with col2:
                            if not task.is_complete:
                                if st.button("Done", key=f"done_{pet.name}_{task.description}_{task.scheduled_time}"):
                                    scheduler.mark_task_complete(pet.name, task.description)
                                    st.rerun()
                        with col3:
                            if st.button("🗑️", key=f"del_{pet.name}_{task.description}_{task.scheduled_time}"):
                                pet.remove_task(task.description)
                                st.rerun()
        else:
            st.info("No tasks scheduled yet.")

with tab3:
    st.subheader("📅 Today's Schedule")
    
    todays_tasks = scheduler.get_todays_tasks()
    
    if not todays_tasks:
        st.info("No tasks scheduled for today. Add some tasks in the Tasks tab!")
    else:
        # Check for conflicts
        conflicts = scheduler.detect_conflicts(todays_tasks)
        if conflicts:
            st.warning("⚠️ **Scheduling Conflicts Detected:**")
            for conflict in conflicts:
                st.markdown(f"- {conflict}")
            st.divider()
        
        # Filter options
        col1, col2 = st.columns(2)
        with col1:
            filter_pet = st.selectbox("Filter by pet", ["All"] + [p.name for p in owner.pets])
        with col2:
            filter_status = st.selectbox("Filter by status", ["All", "Pending", "Completed"])
        
        # Apply filters
        filtered_tasks = todays_tasks
        if filter_pet != "All":
            filtered_tasks = scheduler.filter_by_pet(filtered_tasks, filter_pet)
        if filter_status == "Pending":
            filtered_tasks = scheduler.filter_by_status(filtered_tasks, complete=False)
        elif filter_status == "Completed":
            filtered_tasks = scheduler.filter_by_status(filtered_tasks, complete=True)
        
        # Sort by time
        sorted_tasks = scheduler.sort_by_time(filtered_tasks)
        
        st.divider()
        
        # Display schedule
        if sorted_tasks:
            for task in sorted_tasks:
                status_icon = "✅" if task.is_complete else "⏰"
                priority_badge = {"high": "🔴 HIGH", "medium": "🟡 MEDIUM", "low": "🟢 LOW"}.get(task.priority, "")
                freq_text = f" (repeats {task.frequency})" if task.frequency != "once" else ""
                
                with st.container():
                    col1, col2, col3 = st.columns([1, 4, 2])
                    with col1:
                        st.markdown(f"### {task.scheduled_time}")
                    with col2:
                        st.markdown(f"**{task.description}**")
                        st.caption(f"{task.pet_name} • {task.duration_minutes} min • {priority_badge}{freq_text}")
                    with col3:
                        if task.is_complete:
                            st.success("Done!")
                        else:
                            if st.button("Mark Complete", key=f"complete_{task.pet_name}_{task.description}"):
                                scheduler.mark_task_complete(task.pet_name, task.description)
                                st.rerun()
                    st.divider()
        else:
            st.info("No tasks match the current filters.")
        
        # Summary
        st.subheader("📊 Daily Summary")
        total = len(todays_tasks)
        completed = len([t for t in todays_tasks if t.is_complete])
        pending = total - completed
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Tasks", total)
        with col2:
            st.metric("Completed", completed)
        with col3:
            st.metric("Pending", pending)
        
        if total > 0:
            progress = completed / total
            st.progress(progress, text=f"{int(progress * 100)}% complete")

with tab4:
    st.subheader("Care advisor")
    st.markdown(
        "The advisor reads today's schedule, retrieves species notes from `knowledge/`, "
        "and proposes missing care tasks. Nothing is added until you accept it."
    )
    agent = CarePlanAgent(scheduler, load_retriever())

    if st.button("Generate today's care plan", type="primary"):
        st.session_state.care_plan = agent.build_daily_plan()

    notice = st.session_state.pop("advisor_notice", None)
    if notice:
        st.success(notice)

    plan = st.session_state.get("care_plan")
    if plan:
        for step in plan.trace:
            if step.startswith("Knowledge warning") or step.startswith("No pets"):
                st.warning(step)

        if not plan.suggestions and not plan.rejections:
            st.info("No new tasks to propose. The trace below shows why.")

        for suggestion in plan.suggestions:
            review = "Needs review" if suggestion.needs_human_review else "Ready for you to accept"
            moved = ""
            if suggestion.rescheduled_from:
                moved = f" Moved from {suggestion.rescheduled_from}."
            with st.container(border=True):
                st.markdown(
                    f"**{suggestion.pet_name}: {suggestion.description}** at {suggestion.scheduled_time}"
                )
                st.caption(
                    f"{suggestion.duration_minutes} min · {suggestion.priority} · {suggestion.frequency}"
                    f"{moved}"
                )
                st.markdown(
                    f"Confidence **{suggestion.confidence:.2f}** · {review}  \n"
                    f"Source: `{suggestion.citation_id}` in `{suggestion.source}`"
                )
                st.write(suggestion.rationale)
                if st.button(
                    "Accept and add to schedule",
                    key=f"accept_{suggestion.pet_name}_{suggestion.citation_id}_{suggestion.scheduled_time}",
                ):
                    if agent.apply_suggestion(suggestion):
                        st.session_state.advisor_notice = (
                            f"Added {suggestion.description} for {suggestion.pet_name}."
                        )
                        st.session_state.care_plan = agent.build_daily_plan()
                        st.rerun()
                    else:
                        st.error("That time is no longer free, so the task was not added. Generate the plan again.")

        if plan.rejections:
            st.subheader("Blocked suggestions")
            for rejection in plan.rejections:
                st.error(f"{rejection.pet_name}: {rejection.reason} ({rejection.code})")

        with st.expander("How this plan was made"):
            for index, step in enumerate(plan.trace, start=1):
                st.markdown(f"{index}. {step}")

    st.divider()
    st.subheader("Ask a care question")
    question = st.text_input(
        "Question",
        placeholder="How long should an adult dog walk?",
    )
    pet_options = ["No specific pet"] + [pet.name for pet in owner.pets]
    selected_question_pet = st.selectbox("Answer for", pet_options, key="question_pet")
    if st.button("Ask"):
        pet_name = None if selected_question_pet == "No specific pet" else selected_question_pet
        st.session_state.last_answer = agent.answer_question(question, pet_name)

    answer = st.session_state.get("last_answer")
    if answer:
        if answer.refused:
            st.error(answer.text)
        else:
            st.success(f"Confidence {answer.confidence:.2f} · source `{answer.citation_id}`")
            st.write(answer.text)
        with st.expander("How this answer was checked"):
            for index, step in enumerate(answer.trace, start=1):
                st.markdown(f"{index}. {step}")
